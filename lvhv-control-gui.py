# Ed Callaghan
# All-in-one interface for controlling multiple power supplies
# March 2026

import argparse
import atexit
import functools
import json
import os.path
import queue as pyqueue
import subprocess as sp
import threading
from time import sleep
import tkinter as tk
import tkinter.constants as tkc
from tkinter import ttk
from PowerSupplyServerConnection import PowerSupplyServerConnection

TUNNEL_PROCESSES = []
TUNNEL_LOCK = threading.Lock()

def cleanup_tunnels():
    with TUNNEL_LOCK:
        processes = list(TUNNEL_PROCESSES)
        TUNNEL_PROCESSES.clear()

    for process in processes:
        if process.poll() is None:
            process.terminate()
    for process in processes:
        try:
            process.wait(timeout=2.0)
        except sp.TimeoutExpired:
            process.kill()
            process.wait()

atexit.register(cleanup_tunnels)

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

    def __len__(self):
        self.lock.acquire()
        rv = super(ThreadSafeList, self).__len__()
        self.lock.release()
        return rv

    def pop(self, *args, **kwargs):
        self.lock.acquire()
        rv = super(ThreadSafeList, self).pop(*args, **kwargs)
        self.lock.release()
        return rv

    def clear(self):
        self.lock.acquire()
        rv = super(ThreadSafeList, self).clear()
        self.lock.release()
        return rv

class App(tk.Tk):
    def __init__(self, config, header, offset, queue, hv_defaults):
        super().__init__()
        self.queue = queue
        self.hv_defaults = hv_defaults

        # connect to all power supplies
        self.connections = self.establish_connections(config['connections'], header, offset)

        # set up actual gui
        self.title('Tracker LVHV Control Center')
        #self.geometry('640x360+0+0')
        self.geometry('960x540+0+0')
        self.protocol('WM_DELETE_WINDOW', self.close)
        self.bind('q', lambda event: self.close())

        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill=tkc.BOTH, expand=True)

        self.lv_frame = ttk.Frame(self.notebook, relief=tkc.RIDGE, borderwidth=2)
        self.lv_frame.pack(fill=tkc.BOTH, expand=True)
        self.DrawLV()
        self.notebook.add(self.lv_frame, text='LV')

        self.hv_frame = ttk.Frame(self.notebook)
        self.hv_canvas = tk.Canvas(self.hv_frame)
        self.hv_subframe = ttk.Frame(self.hv_canvas)
        self.hv_window = self.hv_canvas.create_window((0, 0),
                                                      window=self.hv_subframe,
                                                      anchor='nw'
                         )
        self.hv_canvas.bind('<Configure>',
            lambda e: self.hv_canvas.itemconfig(self.hv_window, width=e.width)
        )
        self.hv_canvas.bind_all('<Button-4>',
            lambda e: self.hv_canvas.yview_scroll(-1, 'units')
        )
        self.hv_canvas.bind_all('<Button-5>',
            lambda e: self.hv_canvas.yview_scroll(+1, 'units')
        )
        self.hv_scrollbar = ttk.Scrollbar(self.hv_frame,
                                          orient='vertical',
                                          command=self.hv_canvas.yview
                                         )
        self.hv_canvas.configure(yscrollcommand=self.hv_scrollbar.set)
        self.DrawHV()
        self.hv_subframe.bind('<Configure>',
            lambda e: self.hv_canvas.configure(
                scrollregion=self.hv_canvas.bbox('all')
            )
        )
        self.hv_scrollbar.pack(side='right', fill='y')
        self.hv_canvas.pack(fill=tkc.BOTH, expand=True)
        self.hv_frame.pack(fill=tkc.BOTH, expand=True)
        self.notebook.add(self.hv_frame, text='HV')

        # initiate update loop
        self.after(10, self.update_loop)

    def close(self):
        cleanup_tunnels()
        self.destroy()

    def establish_connections(self, subconfigs, header, offset):
        def connect_and_append(subconfig, header, i, out, errors):
            try:
                connection = connect_to(subconfig, header, offset + i)
                out.append((subconfig, connection))
            except Exception as e:
                errors.append((subconfig, e))
        connections = ThreadSafeList()
        errors = ThreadSafeList()
        threads = []
        for i,subconfig in enumerate(subconfigs):
            thread = threading.Thread(daemon=True,
                                      target=connect_and_append,
                                      args=(subconfig, header, i, connections, errors)
                                     )
            threads.append(thread)

        for thread in threads:
            thread.start()

        while 0 < len(threads):
            for thread in threads:
                thread.join(timeout=0.1)
                if not thread.is_alive():
                    threads.remove(thread)

        if errors:
            cleanup_tunnels()
            messages = [
                '%s: %s' % (subconfig['host'], error)
                for subconfig,error in errors
            ]
            raise RuntimeError(
                'failed to establish power-supply connections:\n'
                + '\n'.join(messages)
            )

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
        self.hv_rows = [RowHV(self.hv_subframe, self.queue, self.hv_defaults, *tup)
                        for tup in self.connections]
        for i,row in enumerate(self.hv_rows):
            row.grid(row=i, column=0, sticky='nsew')
        self.hv_subframe.columnconfigure(0, weight=1)
        #self.hv_subframe.pack(fill='both', expand=True)

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
        self.reset_button = ExternalResetButton(self, 'External reset',
                                                subconfig['host'])
        self.on_button  = PowerControlButton(self, 'On', on, 'green', self.dots)
        self.off_button = PowerControlButton(self, 'Off', off, 'red', self.dots)

        self.columnconfigure(1, weight=1)
        self.push_grid(self.slot)
        self.push_grid(self.station)
        self.push_grid(self.host)
        self.push_grid(self.reset_button)
        self.push_grid(self.on_button)
        self.push_grid(self.off_button)
        self.push_grid(self.dots)

    def push_grid(self, widget):
        widget.grid(row=0, column=self.columns)
        self.columns += 1

class RowHV(ttk.Frame):
    def __init__(self, parent, queue, hv_defaults, subconfig, connection):
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
        checkbox_defaults = hv_checkbox_defaults(subconfig, hv_defaults, 12)
        self.checkboxes = Checkboxes(self, self.queue, checkbox_labels,
                                     checkbox_defaults)
        self.setpoint = SetpointEntry(self)
        self.cancels = ThreadSafeList()
        self.ramp_button = RampButton(self, 'Ramp', connection, self.checkboxes, self.setpoint, self.cancels)
        self.down_button = DownButton(self, 'Down', connection, self.checkboxes, self.cancels)
        self.cancel_button = CancelButton(self, 'Cancel', self.cancels)
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
        self.push_grid(self.cancel_button)

    def push_grid(self, widget, new_row=False):
        if new_row:
            self.rows += 1
            self.columns = 0
        widget.grid(row=self.rows, column=self.columns)
        self.columns += 1

def hv_channels_from(value):
    rv = set()
    if value is None:
        return rv
    if isinstance(value, int):
        value = [value]
    for channel in value:
        try:
            channel = int(channel)
        except Exception:
            continue
        if 0 <= channel < 12:
            rv.add(channel)
    return rv

def hv_checkbox_defaults(subconfig, defaults, channel_count):
    off = set()
    off |= hv_channels_from(defaults.get('off'))
    off |= hv_channels_from(defaults.get('hosts', {}).get(subconfig['host']))
    off |= hv_channels_from(defaults.get('stations', {}).get(str(subconfig['station'])))
    off |= hv_channels_from(defaults.get('slots', {}).get(str(subconfig['slot'])))
    return [(i not in off) for i in range(channel_count)]

class Checkboxes(ttk.Frame):
    def __init__(self, parent, queue, labels, defaults=None):
        super().__init__(parent)
        self.columnconfigure(1, weight=1)
        self.widgets = []
        self.rows = 0
        self.columns = 0
        if defaults is None:
            defaults = [True for _ in labels]
        for i,label in enumerate(labels):
            new_row = False
            if i == 6:
                new_row = True
            widget = Checkbox(self, queue, label, defaults[i])
            self.push_grid(widget, new_row=new_row)
            self.widgets.append(widget)

        for i in range(6):
            self.columnconfigure(i, weight=1, uniform='checks')

    def push_grid(self, widget, new_row=False):
        if new_row:
            self.rows += 1
            self.columns = 0
        widget.grid(row=self.rows, column=self.columns, sticky='w')
        self.columns += 1

class Checkbox(ttk.Checkbutton):
    def __init__(self, parent, queue, label, checked=True):
        self.variable = tk.BooleanVar()
        self.variable.set(checked)
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
        self.cancels = kwargs['cancels']
        fwds = {k: v for k,v in kwargs.items() if k != 'cancels'}
        super().__init__(*args, **fwds)

    def transition(self, connection, channel, voltage):
        print('Ramping channel %d to %.1f V' % (channel, voltage))
        connection.SetWireVoltage(channel, voltage)

    def conditional_transition(self, connection, channel, voltage):
        tripped = connection.QueryTripStatus(channel)
        if tripped:
            return
        self.transition(connection, channel, voltage)

    def ramp(self, reference_connection, checkboxes, voltage):
        self.cancels.clear()

        channels = []
        i = 0
        for i,checkbox in enumerate(checkboxes):
            if checkbox.variable.get():
                channels.append(i)

        host = reference_connection.host
        port = reference_connection.port
        header = reference_connection.header
        #cpath = reference_connection.dac_calibration_path

        set_voltage = lambda *args: self.conditional_transition(*args)
        connections = []
        threads = []
        for channel in channels:
            connection = PowerSupplyServerConnection(host, port, header)
            connections.append(connection)
            self.cancels.append((connection, channel))
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

        self.cancels.clear()

class RampButton(RampableButton):
    def __init__(self, parent, text, connection, checkboxes, setpoint, cancels):
        self.reference_connection = connection
        self.checkboxes = checkboxes.widgets
        self.setpoint = setpoint
        super().__init__(parent, text=text, command=self.spawn_press,
                         cancels=cancels)

    def press(self):
        voltage = self.setpoint.Get()

        if voltage is None:
            print('No setpoint, ramp aborted')
            return # TODO notify of problem
        else:
            self.ramp(self.reference_connection, self.checkboxes, voltage)

    def spawn_press(self):
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class DownButton(RampableButton):
    def __init__(self, parent, text, connection, checkboxes, cancels):
        self.reference_connection = connection
        self.checkboxes = checkboxes.widgets
        super().__init__(parent, text=text, command=self.spawn_press,
                         cancels=cancels)

    def zero_dacs(self):
        host = self.reference_connection.host
        port = self.reference_connection.port
        header = self.reference_connection.header
        #cpath = self.reference_connection.dac_calibration_path
        connection = PowerSupplyServerConnection(host, port, header)
        for i,checkbox in enumerate(self.checkboxes):
            if checkbox.variable.get():
                if not connection.GetHVLock(i):
                    connection._set_hv_by_dac(i, 0)

    def press(self):
        self.ramp(self.reference_connection, self.checkboxes, 20.0)
        self.zero_dacs()

    def spawn_press(self):
        # TODO disable button while ramp in progress, enable cancel
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class CancelButton(ttk.Button):
    def __init__(self, parent, text, cancels):
        super().__init__(parent, text=text, command=self.spawn_press)
        self.cancels = cancels

    def lock_wait_exit(self, connection, channel, wait):
        was = connection.GetHVLock(channel)
        connection.SetHVLock(channel, True)
        sleep(wait)
        connection.SetHVLock(channel, was)

    def press(self):
        while 0 < len(self.cancels):
            connection, channel = self.cancels.pop(0)
            target = lambda *args: self.lock_wait_exit(*args)
            thread = threading.Thread(daemon=True,
                                      target=target,
                                      args=(connection, channel, 10.0)
                                     )
            thread.start()

    def spawn_press(self):
        # TODO disable button while ramp in progress, enable cancel
        thread = threading.Thread(daemon=True,
                                  target=self.press,
                                  args=()
                                 )
        thread.start()

class ExternalResetButton(ttk.Button):
    def __init__(self, parent, text, host):
        self.host = host
        super().__init__(parent, text=text, command=self.spawn_press)

    def call(self):
        cli = 'ssh %s frontend-reset' % self.host
        tok = cli.split(' ')
        sp.run(tok)

    def press(self):
        self.call()

    def spawn_press(self):
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
    tok = [
           'ssh',
           '-o', 'ExitOnForwardFailure=yes',
           '-NL', '%d:localhost:%d' % (local_port, remote_port),
           host,
          ]
    process = sp.Popen(tok)
    with TUNNEL_LOCK:
        TUNNEL_PROCESSES.append(process)
    return process

def load_config(path):
    with open(path, 'r') as f:
        rv = json.load(f)
    return rv

def strip_json_comments(text):
    rv = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        c = text[i]
        n = text[i + 1] if i + 1 < len(text) else ''

        if in_string:
            rv.append(c)
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == '"':
                in_string = False
            i += 1
        elif c == '"':
            in_string = True
            rv.append(c)
            i += 1
        elif c == '#':
            while i < len(text) and text[i] != '\n':
                i += 1
        elif c == '/' and n == '/':
            i += 2
            while i < len(text) and text[i] != '\n':
                i += 1
        elif c == '/' and n == '*':
            i += 2
            while i + 1 < len(text) and not (text[i] == '*' and text[i + 1] == '/'):
                i += 1
            i += 2
        else:
            rv.append(c)
            i += 1

    return ''.join(rv)

def merge_json_value(a, b):
    if isinstance(a, dict) and isinstance(b, dict):
        rv = dict(a)
        for key,value in b.items():
            if key in rv:
                rv[key] = merge_json_value(rv[key], value)
            else:
                rv[key] = value
        return rv
    if isinstance(a, list) and isinstance(b, list):
        return a + b
    return b

def additive_json_object(pairs):
    rv = {}
    for key,value in pairs:
        if key in rv:
            rv[key] = merge_json_value(rv[key], value)
        else:
            rv[key] = value
    return rv

def load_json_with_comments(path):
    with open(path, 'r') as f:
        text = f.read()
    return json.loads(strip_json_comments(text),
                      object_pairs_hook=additive_json_object)

def load_hv_defaults(path):
    if path is None or not os.path.exists(path):
        return {}
    return load_json_with_comments(path)


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

        tunnel = ssh_tunnel(remote, local_port, remote_port)
        sleep(3.0)
        if tunnel.poll() is not None:
            raise RuntimeError(
                'failed to open ssh tunnel %s:%d -> localhost:%d'
                % (remote, remote_port, local_port)
            )

    cpath = None
    if 'calibration' in subconfig.keys():
        cpath = subconfig['calibration']

    rv = ThreadSafePowerSupplyServerConnection(host, port, header, cpath)
    return rv

def main(args):
    config = load_config(args.cpath)
    hv_defaults = load_hv_defaults(args.hv_defaults)
    queue = pyqueue.Queue()
    app = App(config, args.header, args.offset, queue, hv_defaults)
    app.mainloop()
    exit(0)

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', type=str, dest='cpath', required=True)
    parser.add_argument('--header', type=str, dest='header', required=True)
    parser.add_argument('--port-offset', type=int, dest='offset', default=0)
    parser.add_argument('--hv-off-defaults', type=str, dest='hv_defaults',
                        default='./hv-off-defaults.json')

    args = parser.parse_args()
    main(args)
