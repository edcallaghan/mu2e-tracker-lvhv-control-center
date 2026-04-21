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
    def __init__(self, config, header, offset, queue):
        super().__init__()
        self.queue = queue

        # connect to all power supplies
        self.connections = self.establish_connections(config['connections'], header, offset)

        # set up actual gui
        self.title('Tracker LVHV Control Center')
        #self.geometry('640x360+0+0')
        self.geometry('960x540+0+0')
        self.bind('q', lambda event: self.destroy())

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tkc.BOTH, expand=True)

        self.lv_frame = ttk.Frame(self.notebook, relief=tkc.RIDGE, borderwidth=2)
        self.lv_frame.pack(fill=tkc.BOTH, expand=True)
        self.DrawLV()
        self.notebook.add(self.lv_frame, text='LV')

        self.hv_frame = ttk.Frame(self.notebook)
        self.hv_frame.pack(fill=tkc.BOTH, expand=True)
        self.DrawHV()
        self.notebook.add(self.hv_frame, text='HV')

        # initiate update loop
        self.after(10, self.update_loop)

    def establish_connections(self, subconfigs, header, offset):
        def connect_and_append(subconfig, header, i, out):
            connection = connect_to(subconfig, header, offset + i)
            out.append((subconfig, connection))
        connections = ThreadSafeList()
        threads = []
        for i,subconfig in enumerate(subconfigs):
            thread = threading.Thread(daemon=True,
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

    def DrawLV(self):
        self.lv_rows = [RowLV(self.lv_frame, self.queue, *tup)
                        for tup in self.connections]
        for i,row in enumerate(self.lv_rows):
            row.grid(row=i, column=0, sticky='nsew')
        self.lv_frame.columnconfigure(0, weight=1)
        self.lv_frame.pack(fill='both', expand=True)

    def DrawHV(self):
        self.hv_rows = [RowHV(self.hv_frame, self.queue, *tup)
                        for tup in self.connections]
        for i,row in enumerate(self.hv_rows):
            row.grid(row=i, column=0, sticky='nsew')
        self.hv_frame.columnconfigure(0, weight=1)
        self.hv_frame.pack(fill='both', expand=True)

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

class RowLV(ttk.Frame):
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
        self.dots = DotsLV(self, self.queue, self.connection)
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

class RowHV(ttk.Frame):
    def __init__(self, parent, queue, subconfig, connection):
        super().__init__(parent)
        self.queue = queue
        self.connection = connection
        self.rows = 0
        self.columns = 0

        self.slot    = ttk.Label(self,
                                 text='Slot %02d' % subconfig['slot'])
        self.station = ttk.Label(self,
                                 text='Station %02d' % subconfig['station'])
        self.host    = ttk.Label(self,
                                 text='%s' % subconfig['host'],
                                 anchor='e')
        checkbox_labels = ['%d' % i for i in range(12)]
        self.checkboxes = Checkboxes(self, self.queue, checkbox_labels)
        self.setpoint = SetpointEntry(self)
        self.ramp_button = RampButton(self, 'Ramp', connection, self.checkboxes, self.setpoint)
        self.down_button = DownButton(self, 'Down', connection, self.checkboxes)
        self.dots = DotsHV(self, self.queue, self.connection)

        self.columnconfigure(1, weight=1)
        self.push_grid(self.slot)
        self.push_grid(self.station)
        self.push_grid(self.host)
        self.push_grid(self.checkboxes)
        self.push_grid(self.setpoint)
        self.push_grid(self.ramp_button)
        self.push_grid(self.down_button)
        self.push_grid(self.dots, new_row=False)

    def push_grid(self, widget, new_row=False):
        if new_row:
            self.rows += 1
            self.columns = 0
        widget.grid(row=self.rows, column=self.columns)
        self.columns += 1

class Checkboxes(ttk.Frame):
    def __init__(self, parent, queue, labels):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self.widgets = []
        self.rows = 0
        self.columns = 0
        for i,label in enumerate(labels):
            new_row = False
            if i == 6:
                new_row = True
            widget = Checkbox(self, queue, label)
            self.push_grid(widget, new_row=new_row)
            self.widgets.append(widget)

    def push_grid(self, widget, new_row=False):
        if new_row:
            self.rows += 1
            self.columns = 0
        widget.grid(row=self.rows, column=self.columns)
        self.columns += 1

class Checkbox(ttk.Checkbutton):
    def __init__(self, parent, queue, label):
        self.variable = tk.BooleanVar()
        super().__init__(parent, text=label, variable=self.variable)

class SetpointEntry(ttk.Entry):
    def __init__(self, parent):
        super().__init__(parent)
        self.insert(0, '12.0')

    def Get(self):
        text = self.get()
        rv = None
        try:
            rv = float(text)
        except Exception as e:
            print('setpoint exception: %s' % str(e))
            rv = None

        if rv is not None:
            if rv < 0.0:
                print('invalid setpoint: %f' % rv)
                rv = None
            elif 1450.0 < rv:
                print('invalid setpoint: %f' % rv)
                rv = None

        return rv

class RampableButton(ttk.Button):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

    def transition(self, connection, channel, voltage):
        print('Ramping channel %d to %.1f V' % (channel, voltage))
        connection.SetWireVoltage(channel, voltage)

    def conditional_transition(self, connection, channel, voltage):
        tripped = connection.QueryTripStatus(channel)
        if tripped:
            return
        self.transition(connection, channel, voltage)

    def ramp(self, reference_connection, checkboxes, voltage):
        channels = []
        i = 0
        for i,checkbox in enumerate(checkboxes):
            if checkbox.variable.get():
                channels.append(i)

        host = reference_connection.host
        port = reference_connection.port
        header = reference_connection.header
        cpath = reference_connection.dac_calibration_path

        set_voltage = lambda *args: self.conditional_transition(*args)
        connections = []
        threads = []
        for channel in channels:
            connection = PowerSupplyServerConnection(host, port, header, cpath)
            connections.append(connection)
            thread = threading.Thread(daemon=True,
                                      target=set_voltage,
                                      args=(connection, channel, voltage),
                                     )
            threads.append(thread)

        for thread in threads:
            thread.start()

        done = False
        while not done and 0 < len(threads):
            for thread in threads:
                thread.join(timeout=0.1)
                if thread.is_alive():
                    done &= False
                else:
                    done &= True
                    threads.remove(thread)

        for connection in connections:
            connection.close()

class RampButton(RampableButton):
    def __init__(self, parent, text, connection, checkboxes, setpoint):
        self.reference_connection = connection
        self.checkboxes = checkboxes.widgets
        self.setpoint = setpoint
        super().__init__(parent, text=text, command=self.spawn_press)

    def press(self):
        voltage = self.setpoint.Get()

        if voltage is None:
            print('No setpoint, ramp aborted')
            return # TODO notify of problem
        else:
            self.ramp(self.reference_connection, self.checkboxes, voltage)

    def spawn_press(self):
        # TODO disable button while ramp in progress, enable cancel
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class DownButton(RampableButton):
    def __init__(self, parent, text, connection, checkboxes):
        self.reference_connection = connection
        self.checkboxes = checkboxes.widgets
        super().__init__(parent, text=text, command=self.spawn_press)

    def zero_dacs(self):
        host = self.reference_connection.host
        port = self.reference_connection.port
        header = self.reference_connection.header
        cpath = self.reference_connection.dac_calibration_path
        connection = PowerSupplyServerConnection(host, port, header, cpath)
        for i,checkbox in enumerate(self.checkboxes):
            if checkbox.variable.get():
                connection._set_hv_by_dac(i, 0)

    def press(self):
        self.ramp(self.reference_connection, self.checkboxes, 50.0)
        self.zero_dacs()

    def spawn_press(self):
        # TODO disable button while ramp in progress, enable cancel
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

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
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class DotsLV(ttk.Frame):
    def __init__(self, parent, queue, connection):
        super().__init__(parent)
        self.queue = queue
        self.connection = connection
        self.columns = 0

        self.dots = []
        for i in range(6):
            dot = DotLV(self, self.queue, self.connection, i, 'red', 16)
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

class DotLV(tk.Canvas):
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
        thread = threading.Thread(daemon=True,
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
        thread = threading.Thread(daemon=True,
                                  target=self.toggle,
                                  args=()
                                 )
        thread.start()

    def _on_click(self, event):
        self.spawn_toggle()

def query_hv_trip_status(connection, channel):
    rv = None
    try:
        tripped = connection.QueryTripStatus(channel)
        if tripped:
            rv = True
        else:
            rv = False
    except Exception as e:
        pass
    return rv

class DotsHV(ttk.Frame):
    def __init__(self, parent, queue, connection):
        super().__init__(parent)
        self.queue = queue
        self.connection = connection
        self.rows = 0
        self.columns = 0

        self.dots = []
        for i in range(12):
            dot = DotHV(self, self.queue, self.connection, i, 'red', 16)
            self.dots.append(dot)

        for i,dot in enumerate(self.dots):
            new_row=False
            if i == 6:
                new_row = True
            self.push_grid(dot, new_row=new_row)

    def push_grid(self, widget, new_row=False):
        if new_row:
            self.rows += 1
            self.columns = 0
        widget.grid(row=self.rows, column=self.columns)
        self.columns += 1

    def push_recolor(self, color):
        f = lambda w: w.recolor(color)
        tups = [(f,dot) for dot in self.dots]
        self.queue.put_nowait(tups)

def poll_hv_trip_status(dot, interval):
    stop = False
    while not stop:
        is_tripped = query_hv_trip_status(dot.connection, dot.channel)
        if is_tripped is None:
            dot.push_recolor('yellow')
        elif is_tripped:
            dot.push_recolor('red')
        else:
            dot.push_recolor('green')
        sleep(interval)

def zero_dac_and_reset_trip(connection, channels):
    for channel in channels:
        print('Reset trip channel %d' % channel)
        connection._set_hv_by_dac(channel, 0)
        sleep(1.0)
        connection.ResetTripStatus(channel)

class DotHV(tk.Canvas):
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
        thread = threading.Thread(daemon=True,
                                  target=poll_hv_trip_status,
                                  args=(self, interval)
                                 )
        thread.start()

    def toggle(self):
        if self.color == 'red':
            self.push_recolor('green')
            zero_dac_and_reset_trip(self.connection, [self.channel])
        elif self.color == 'green':
            # TODO force trip
            '''

            self.push_recolor('red')
            force_trip(self.connection, [self.channel])
            '''
            pass

    def spawn_toggle(self):
        thread = threading.Thread(daemon=True,
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

    cpath = None
    if 'calibration' in subconfig.keys():
        cpath = subconfig['calibration']

    rv = ThreadSafePowerSupplyServerConnection(host, port, header, cpath)
    return rv

def main(args):
    config = load_config(args.cpath)
    queue = pyqueue.Queue()
    app = App(config, args.header, args.offset, queue)
    app.mainloop()
    exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', type=str, dest='cpath', required=True)
    parser.add_argument('--header', type=str, dest='header', required=True)
    parser.add_argument('--port-offset', type=int, dest='offset', default=0)

    args = parser.parse_args()
    main(args)
