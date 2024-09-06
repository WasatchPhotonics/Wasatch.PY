# This is a simple unit-test designed to see whether multiprocessing Queues are 
# leaking on Linux.  They don't seem to be, which is good for most people, but
# leaves me still with an unexplained leak.

import os
import sys
import time
import Queue
import psutil
import random
import datetime
import multiprocessing

max_readings = 10000

class Reading:
    count = 0

    def __init__(self):
        Reading.count += 1

        self.id = Reading.count
        self.timestamp = datetime.datetime.now()
        self.spectrum                  = [int(65536 * random.random()) for x in range(1024)]
        self.laser_temperature_raw     = int(self.rand(0, 4096))
        self.laser_temperature_degC    = int(self.rand(20, 40))
        self.detector_temperature_raw  = int(self.rand(0, 4096))
        self.detector_temperature_degC = int(self.rand(-20, 60))
        self.secondary_adc_raw         = int(self.rand(0, 4096))
        self.secondary_adc_calibrated  = float(self.rand(0, 100))
        self.laser_status              = None   
        self.laser_power               = int(self.rand(0, 100))
        self.laser_power_in_mW         = True
        self.failure                   = None
        self.averaged                  = False
        self.session_count             = self.id
        self.area_scan_row_count       = int(self.rand(0, 1024))
        self.battery_raw               = int(self.rand(0, 4096))
        self.battery_percentage        = int(self.rand(0, 100))
        self.battery_charging          = None

    def rand(self, lo, hi):
        return lo + (hi - lo) * random.random()

class SubprocessArgs:
    def __init__(self, response_queue):
        self.response_queue = response_queue

class Wrapper:
    def __init__(self):
        self.manager = multiprocessing.Manager()
        self.response_queue = self.manager.Queue(100) 
        self.poller = None 

    def connect(self):
        subprocessArgs = SubprocessArgs(response_queue = self.response_queue)
        self.poller = multiprocessing.Process(target=self.continuous_poll, args=(subprocessArgs,))
        self.poller.start()

    def acquire_data(self):
        reading = None
        last_reading = None
        dequeue_count = 0
        while True:
            try:
                reading = self.response_queue.get_nowait()
                if reading is None:
                    # nothing in the queue
                    return None
                elif isinstance(reading, bool):
                    return reading
                else:
                    print(f"acquire_data: read Reading {reading.id}")
                    dequeue_count += 1
                    last_reading = reading
            except Queue.Empty:
                break

        if dequeue_count > 1:
            print(f"acquire_data: discarded {dequeue_count - 1} readings")

        return last_reading

    def continuous_poll(self, args):
        pid = os.getpid()
        print(f"worker: entering loop in process {pid}")
        count = 0
        while True:
            # sleep_sec = 0.01 + (.01 * random.random())
            # print "worker: sleeping %.2f sec" % sleep_sec
            # time.sleep(sleep_sec)

            reading = Reading()
            print(f"worker: enqueuing reading {reading.id}")
            args.response_queue.put(reading, timeout=1)
            count += 1

            if count >= max_readings:
                print(f"worker: enqueued {count}, readings, quitting")
                break
                
        print("worker: sending poison-pill")
        args.response_queue.put(True, timeout=1)

        print("worker: exiting")
        sys.exit()

parent_pid = os.getpid()
print(f"Main: Running from pid {parent_pid}")

print("Main: instantiating Wrapper")
wrapper = Wrapper()

print("Main: connecting to background process")
wrapper.connect()

print("Main: reading spectra")
while True:
    reading = wrapper.acquire_data()
    if reading is None:
        print("Main: no reading available")
    elif isinstance(reading, bool) and reading:
        print("Main: received poison-pill, exiting")
        break
    else:
        print("Main: received reading %d (%s)" % (reading.id, reading.spectrum[:10]))

    size_in_bytes = psutil.Process(parent_pid).memory_info().rss
    print("Main: memory = %d bytes" % size_in_bytes)

    print("Main: sleeping 1 sec")
    time.sleep(1)

print("Main: exiting")
