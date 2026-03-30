# Ed Callaghan
# All-in-one interface for controlling multiple power supplies
# March 2026

import argparse
import functools
import json
import queue as pyqueue
import subprocess as sp
import threading
from time import sleep
import tkinter as tk
import tkinter.constants as tkc
from tkinter import ttk
from PowerSupplyServerConnection import PowerSupplyServerConnection

# thank you, AI :)
def threadsafe(cls):
    for name in dir(cls):
        attr = getattr(cls, name)
        if callable(attr) and not name.startswith("__"):
            @functools.wraps(attr)
            def wrapper(self, *args, __attr=attr, **kwargs):
                self._lock.acquire()
                rv = __attr(self, *args, **kwargs)
                self._lock.release()
                return rv
            setattr(cls, name, wrapper)
    return cls

@threadsafe
class ThreadSafePowerSupplyServerConnection(PowerSupplyServerConnection):
    def __init__(self, *args, **kwargs):
        self._lock = threading.RLock()
        super().__init__(*args, **kwargs)

class ThreadSafeList(list):
    def __init__(self):
        super().__init__(self)
        self.lock = threading.Lock()

    def append(self, item):
        self.lock.acquire()
        super(ThreadSafeList, self).append(item)
        self.lock.release()

class App(tk.Tk):
    def __init__(self, config, header, queue):
        super().__init__()
        self.queue = queue

        # connect to all power supplies
        self.connections = self.establish_connections(config['connections'], header)

        # set up actual gui
        self.frame = ttk.Frame(self, relief=tkc.RIDGE, borderwidth=2)
        self.frame.pack(fill=tkc.BOTH, expand=1)

        self.title('Tracker LVHV Control Center')
        self.geometry('640x360+0+0')
        self.bind('q', lambda event: self.destroy())

        self.Draw()

        # initiate update loop
        self.after(10, self.update_loop)

    def establish_connections(self, subconfigs, header):
        def connect_and_append(subconfig, header, i, out):
            connection = connect_to(subconfig, header, i)
            out.append((subconfig, connection))
        connections = ThreadSafeList()
        threads = []
        for i,subconfig in enumerate(subconfigs):
            thread = threading.Thread(daemon=False,
                                      target=connect_and_append,
                                      args=(subconfig, header, i, connections)
                                     )
            threads.append(thread)

        for thread in threads:
            thread.start()

        while 0 < len(threads):
            for thread in threads:
                thread.join(timeout=0.1)
                if not thread.is_alive():
                    threads.remove(thread)

        rv = sorted(connections, key=lambda pair: pair[0]['slot'])
        return rv

    def Draw(self):
        self.rows = [Row(self.frame, self.queue, *tup)
                        for tup in self.connections]
        for i,row in enumerate(self.rows):
            row.grid(row=i, column=0, sticky='nsew')
        self.frame.columnconfigure(0, weight=1)
        self.frame.pack(fill='both', expand=True)

    def update_loop(self):
        while True:
            try:
                tups = self.queue.get_nowait()
                for f,w in tups:
                    f(w)
            except pyqueue.Empty:
                break
        self.after(100, self.update_loop)

def power_on(connection, channels):
    for channel in channels:
        print('Channel %d on' % channel)
        connection.EnableLowVoltage(channel)

def power_off(connection, channels):
    if len(channels) < 1:
        print('Global off')
        connection.DisableLowVoltage()
    else:
        for channel in channels:
            print('Channel %d off' % channel)
            connection.DisableLowVoltage(channel)

def query_power(connection, channel):
    voltage = connection.QueryPowerVoltage(channel)
    rv = (40.0 < voltage)
    if voltage < -50.0:
        rv = None
    return rv

class Row(ttk.Frame):
    def __init__(self, parent, queue, subconfig, connection):
        super().__init__(parent)
        self.queue = queue
        self.connection = connection
        self.columns = 0

        self.slot    = ttk.Label(self,
                                 text='Slot %02d' % subconfig['slot'])
        self.station = ttk.Label(self,
                                 text='Station %02d' % subconfig['station'])
        self.host    = ttk.Label(self,
                                 text='%s' % subconfig['host'],
                                 anchor='e')
        self.dots = Dots(self, self.queue, self.connection)
        on = lambda: power_on(self.connection, range(6))
        off = lambda: power_off(self.connection, [])
        self.on_button  = PowerControlButton(self, 'On', on, 'green', self.dots)
        self.off_button = PowerControlButton(self, 'Off', off, 'red', self.dots)

        self.columnconfigure(1, weight=1)
        self.push_grid(self.slot)
        self.push_grid(self.station)
        self.push_grid(self.host)
        self.push_grid(self.on_button)
        self.push_grid(self.off_button)
        self.push_grid(self.dots)

    def push_grid(self, widget):
        widget.grid(row=0, column=self.columns)
        self.columns += 1

class PowerControlButton(ttk.Button):
    def __init__(self, parent, text, call, color, dots):
        self.call = call
        self.color = color
        self.dots = dots
        super().__init__(parent, text=text, command=self.spawn_press)

    def press(self):
        self.call()
        self.dots.push_recolor(self.color)

    def spawn_press(self):
        thread = threading.Thread(daemon=False,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class Dots(ttk.Frame):
    def __init__(self, parent, queue, connection):
        super().__init__(parent)
        self.queue = queue
        self.connection = connection
        self.columns = 0

        self.dots = []
        for i in range(6):
            dot = Dot(self, self.queue, self.connection, i, 'red', 16)
            self.dots.append(dot)

        for dot in self.dots:
            self.push_grid(dot)

    def push_grid(self, widget):
        widget.grid(row=0, column=self.columns)
        self.columns += 1

    def push_recolor(self, color): 
        f = lambda w: w.recolor(color)
        tups = [(f,dot) for dot in self.dots]
        self.queue.put_nowait(tups)

def poll_power_on(dot, interval):
    stop = False
    while not stop:
        is_on = query_power(dot.connection, dot.channel)
        if is_on is None:
            dot.push_recolor('yellow')
        elif is_on:
            dot.push_recolor('green')
        else:
            dot.push_recolor('red')
        sleep(interval)

class Dot(tk.Canvas):
    def __init__(self, parent, queue, connection, channel, color, size):
        super().__init__(parent, width=size, height=size, highlightthickness=0)
        self.queue = queue
        self.connection = connection
        self.channel = channel
        self.item = self.create_oval(2, 2, size-2, size-2, fill=color, outline='')
        self.color = color

        self.bind('<Button-1>', self._on_click)
        self.begin_polling(1.0)

    def recolor(self, color):
        self.itemconfig(self.item, fill=color)
        self.color = color

    def push_recolor(self, color):
        if color != self.color:
            f = lambda w: w.recolor(color)
            self.queue.put_nowait(((f, self),))

    def begin_polling(self, interval):
        thread = threading.Thread(daemon=False,
                                  target=poll_power_on,
                                  args=(self, interval)
                                 )
        thread.start()

    def toggle(self):
        if self.color == 'red':
            self.push_recolor('green')
            power_on(self.connection, [self.channel])
        elif self.color == 'green':
            self.push_recolor('red')
            power_off(self.connection, [self.channel])

    def spawn_toggle(self):
        thread = threading.Thread(daemon=False,
                                  target=self.toggle,
                                  args=()
                                 )
        thread.start()

    def _on_click(self, event):
        self.spawn_toggle()

def ssh_tunnel(host, local_port, remote_port):
    cli = 'ssh -NL %d:localhost:%d %s' % (local_port, remote_port, host)
    tok = cli.split(' ')
    sp.run(tok)

def load_config(path):
    with open(path, 'r') as f:
        rv = json.load(f)
    return rv

def connect_to(subconfig, header, offset):
    if not subconfig['tunnel']:
        host = subconfig['host']
        port = subconfig['port']
    else:
        remote = subconfig['host']
        remote_port = subconfig['port']
        local_port = remote_port + offset
        host = 'localhost'
        port = local_port

        thread = threading.Thread(name='%s tunnel' % remote,
                                  daemon=False,
                                  target=ssh_tunnel,
                                  args=(remote, local_port, remote_port)
                                 )
        thread.start()
        sleep(3.0)

    rv = ThreadSafePowerSupplyServerConnection(host, port, header)
    return rv

def main(args):
    config = load_config(args.cpath)
    queue = pyqueue.Queue()
    app = App(config, args.header, queue)
    app.mainloop()
    exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', type=str, dest='cpath', required=True)
    parser.add_argument('--header', type=str, dest='header', required=True)

    args = parser.parse_args()
    main(args)
