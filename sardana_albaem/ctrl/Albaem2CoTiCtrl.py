#!/usr/bin/env python
import socket
import time
from threading import Lock

from sardana import State, DataAccess
from sardana.pool import AcqSynch
from sardana.pool.controller import CounterTimerController, Type, Access, \
    Description, Memorize, Memorized, NotMemorized
from sardana.sardanavalue import SardanaValue

__all__ = ['Albaem2CoTiCtrl']

TRIGGER_INPUTS = {'DIO_1': 0, 'DIO_2': 1, 'DIO_3': 2, 'DIO_4': 3,
                  'DIFF_IO_1': 4, 'DIFF_IO_2': 5, 'DIFF_IO_3': 6,
                  'DIFF_IO_4': 7, 'DIFF_IO_5': 8, 'DIFF_IO_6': 9,
                  'DIFF_IO_7': 10, 'DIFF_IO_8': 11, 'DIFF_IO_9': 12}


class Albaem2CoTiCtrl(CounterTimerController):
    MaxDevice = 5

    ctrl_properties = {
        'AlbaEmHost': {
            Description: 'AlbaEm Host name',
            Type: str
        },
        'Port': {
            Description: 'AlbaEm Host name',
            Type: int
        },
        'ExtTriggerInput': {
            Description: 'ExtTriggerInput',
            Type: str
        },
    }

    ctrl_attributes = {
        'AcquisitionMode': {
            Type: str,
            # TODO define the modes names ?? (I_AVGCURR_A, Q_CHARGE_C)
            Description: 'Acquisition Mode: CHARGE, INTEGRATION',
            Access: DataAccess.ReadWrite,
            Memorize: Memorized
        },
    }

    axis_attributes = {
        "Range": {
            Type: str,
            Description: 'Range for the channel',
            Memorize: NotMemorized,
            Access: DataAccess.ReadWrite,
        },
        "Inversion": {
            Type: bool,
            Description: 'Channel Digital inversion',
            Memorize: NotMemorized,
            Access: DataAccess.ReadWrite,

        },
        "InstantCurrent": {
            Type: float,
            Description: 'Channel instant current',
            Memorize: NotMemorized,
            Access: DataAccess.ReadOnly
        },
        "FORMULA":
            {
                Type: str,
                Description: 'The formula to get the real value.\n '
                             'e.g. "(value/10)*1e-06"',
                Access: DataAccess.ReadWrite
            },
    }

    def __init__(self, inst, props, *args, **kwargs):
        """Class initialization."""
        CounterTimerController.__init__(self, inst, props, *args, **kwargs)
        msg = "__init__(%s, %s): Entering...", repr(inst), repr(props)
        self._log.debug(msg)

        self.ip_config = (self.AlbaEmHost, self.Port)
        self.albaem_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.albaem_socket.settimeout(1)
        self.albaem_socket.connect(self.ip_config)
        self.index = 0
        self.master = None
        self._latency_time = 0.001  # In fact, it is just 320us
        self._repetitions = 0
        self.formulas = {1: 'value', 2: 'value', 3: 'value', 4:'value'}

        self.lock = Lock()

    def AddDevice(self, axis):
        """Add device to controller."""
        self._log.debug("AddDevice(%d): Entering...", axis)
        # count buffer for the continuous scan
        if axis != 1:
            self.index = 0

    def DeleteDevice(self, axis):
        """Delete device from the controller."""
        self._log.debug("DeleteDevice(%d): Entering...", axis)
        # self.albaem_socket.close()

    def StateAll(self):
        """Read state of all axis."""
        # self._log.debug("StateAll(): Entering...")
        state = self.sendCmd('ACQU:STAT?')

        if state in ['STATE_ACQUIRING', 'STATE_RUNNING']:
            self.state = State.Moving

        elif state == 'STATE_ON':
            self.state = State.On

        elif state == 'STATE_FAULT':
            self.state = State.Fault

        else:
            self.state = State.Fault
            self._log.debug("StateAll(): %r %r UNKNWON STATE: "
                            "%s" % self.state, self.status, state)
        self.status = state
        # self._log.debug("StateAll(): %r %r" %(self.state, self.status))

    def StateOne(self, axis):
        """Read state of one axis."""
        # self._log.debug("StateOne(%d): Entering...", axis)
        return self.state, self.status

    def LoadOne(self, axis, value, repetitions, latency_time):
        # self._log.debug("LoadOne(%d, %f, %d): Entering...", axis, value,
        #                 repetitions)
        if axis != 1:
            raise Exception('The master channel should be the axis 1')

        self.itime = value
        self.index = 0

        # Set Integration time in ms
        val = self.itime * 1000
        if val < 0.1:   # minimum integration time 
            self._log.debug("The minimum integration time is 0.1 ms")
            val = 0.1
        self.sendCmd('ACQU:TIME %r' % val)

        if self._synchronization in [AcqSynch.SoftwareTrigger,
                                     AcqSynch.SoftwareGate]:
            # self._log.debug("SetCtrlPar(): setting synchronization "
            #                 "to SoftwareTrigger")
            self._repetitions = 1
            source = 'SOFTWARE'

        elif self._synchronization == AcqSynch.HardwareTrigger:
            # self._log.debug("SetCtrlPar(): setting synchronization "
            #                 "to HardwareTrigger")
            source = 'HARDWARE'
            self._repetitions = repetitions
        elif self._synchronization == AcqSynch.HardwareGate:
            # self._log.debug("SetCtrlPar(): setting synchronization "
            #                 "to HardwareGate")
            source = 'GATE'
            self._repetitions = repetitions
        self.sendCmd('TRIG:MODE %s' % source)
        if self._synchronization in [AcqSynch.HardwareTrigger,
                                     AcqSynch.HardwareGate]:
            self.sendCmd('TRIG:INPU %s' % self.ExtTriggerInput)
        # Set Number of Triggers
        self.sendCmd('ACQU:NTRI %r' % self._repetitions)

    def PreStartOne(self, axis, value=None):
        # self._log.debug("PreStartOneCT(%d): Entering...", axis)
        if axis != 1:
            self.index = 0

        #Check if the communication is stable before start
        state = self.sendCmd('ACQU:STAT?')
        if state is None:
            return False

        return True

    def StartAll(self):
        """
        Starting the acquisition is done only if before was called
        PreStartOneCT for master channel.
        """
        # self._log.debug("StartAllCT(): Entering...")
        cmd = 'ACQU:START'
        if self._synchronization in [AcqSynch.SoftwareTrigger,
                                     AcqSynch.SoftwareGate]:
            # The HW needs the software trigger
            # APPEND SWTRIG TO THE START COMMAND OR SEND ANOTHER COMMAND
            # TRIG:SWSEt
            cmd += ' SWTRIG'

        self.sendCmd(cmd)
        # THIS PROTECTION HAS TO BE REVIEWED
        # FAST INTEGRATION TIMES MAY RAISE WRONG EXCEPTIONS
        # e.g. 10ms ACQTIME -> self.state MAY BE NOT MOVING BECAUSE
        # FINISHED, NOT FAILED
        self.StateAll()
        t0 = time.time()
        while (self.state != State.Moving):
            if time.time() - t0 > 3:
                raise Exception('The HW did not start the acquisition')
            self.StateAll()
        return True

    def ReadAll(self):
        # self._log.debug("ReadAll(): Entering...")
        # TODO Change the ACQU:MEAS command by CHAN:CURR
        data_ready = int(self.sendCmd('ACQU:NDAT?'))
        self.new_data = []
        try:
            if self.index < data_ready:
                data_len = data_ready - self.index
                # THIS CONTROLLER IS NOT YET READY FOR TIMESTAMP DATA
                self.sendCmd('TMST 0')

                msg = 'ACQU:MEAS? %r,%r' % (self.index - 1, data_len)
                raw_data = self.sendCmd(msg)

                data = eval(raw_data)
                axis = 1
                for chn_name, values in data:

                    # Apply the formula for each value
                    formula = self.formulas[axis]
                    formula = formula.lower()
                    values_formula = [eval(formula, {'value': val}) for val
                                      in values]
                    self.new_data.append(values_formula)
                    axis +=1
                time_data = [self.itime] * len(self.new_data[0])
                self.new_data.insert(0, time_data)
                if self._repetitions != 1:
                    self.index += len(time_data)

        except Exception as e:
            raise Exception("ReadAll error: %s: " + str(e))

    def ReadOne(self, axis):
        # self._log.debug("ReadOne(%d): Entering...", axis)
        if len(self.new_data) == 0:
            return []

        if self._synchronization in [AcqSynch.SoftwareTrigger,
                                     AcqSynch.SoftwareGate]:
            return SardanaValue(self.new_data[axis - 1][0])
        else:
            val = self.new_data[axis - 1]
            return val

    def AbortOne(self, axis):
        # self._log.debug("AbortOne(%d): Entering...", axis)
        self.sendCmd('ACQU:STOP')

    def sendCmd(self, cmd, rw=True, size=8096):
        with self.lock:
            cmd += ';\n'

            # Protection in case of reconnect the device in the network.
            # It send the command and in case of broken socket it creates a
            # new one.
            retries = 2
            for i in range(retries):
                try:
                    self.albaem_socket.sendall(cmd.encode())
                    break
                except socket.timeout:
                    self._log.debug(
                        'Socket timeout! reconnecting and commanding '
                        'again %s' % cmd)
                    self.albaem_socket = socket.socket(
                        socket.AF_INET, socket.SOCK_STREAM)
                    self.albaem_socket.settimeout(1)
                    self.albaem_socket.connect(self.ip_config)
            if rw:
                # WARNING...
                # socket.recv(size) IS NEVER ENOUGH TO RECEIVE DATA !!!
                # you should know by the protocol either:
                # the length of data to be received
                # or
                # wait until a special end-of-transfer control
                # In this case: while not '\r' in data:
                #                 receive more data...
                ################################################
                # AS IT IS SAID IN https://docs.python.org/3/howto/sockets.html
                # SECTION "3 Using a Socket"
                #
                # A protocol like HTTP uses a socket for only one
                # transfer. The client sends a request, the reads a
                # reply. That's it. The socket is discarded. This
                # means that a client can detect the end of the reply
                # by receiving 0 bytes.
                #
                # But if you plan to reuse your socket for further
                # transfers, you need to realize that there is no
                # "EOT" (End of Transfer) on a socket. I repeat: if a
                # socket send or recv returns after handling 0 bytes,
                # the connection has been broken. If the connection
                # has not been broken, you may wait on a recv forever,
                # because the socket will not tell you that there's
                # nothing more to read (for now). Now if you think
                # about that a bit, you'll come to realize a
                # fundamental truth of sockets: messages must either
                # be fixed length (yuck), or be delimited (shrug), or
                # indicate how long they are (much better), or end by
                # shutting down the connection. The choice is entirely
                # yours, (but some ways are righter than others).
                ################################################
                data = ""
                acquired = False
                while True:
                    # SOME TIMEOUTS OCCUR WHEN USING THE WEBPAGE
                    retries = 5
                    for i in range(retries):
                        try:
                            data += self.albaem_socket.recv(size).decode()
                            acquired = True
                            break
                        except socket.timeout:
                            self._log.debug(
                                'Socket timeout! Reading... from  %s '
                                'command' %cmd[:-2])
                            self.albaem_socket = socket.socket(
                                socket.AF_INET, socket.SOCK_STREAM)
                            self.albaem_socket.settimeout(1)
                            self.albaem_socket.connect(self.ip_config)
                            self.albaem_socket.sendall(cmd.encode())
                            pass

                    if acquired == False:
                        msg = "Unable to communicate with AlbaEm2, try to " \
                              "restart the Device"
                        raise RuntimeError(msg)
                    try:
                        if data[-1] == '\n':
                            break
                    except Exception as e:
                        self._log.error(e)
                        return None

                # NOTE: EM MAY ANSWER WITH MULTIPLE ANSWERS IN CASE OF AN
                # EXCEPTION
                # SIMPLY GET THE LAST ONE
                if data.count(';') > 1:
                    data = data.rsplit(';')[-2:]
                return data[:-2]

###############################################################################
#                Axis Extra Attribute Methods
###############################################################################

    def GetAxisExtraPar(self, axis, name):
        self._log.debug("GetExtraAttributePar(%d, %s): Entering...", axis,
                        name)
        if axis == 1:
            raise ValueError('The axis 1 does not use the extra attributes')

        name = name.lower()
        axis -= 1
        if name == "range":
            cmd = 'CHAN{0:02d}:CABO:RANGE?'.format(axis)
            return self.sendCmd(cmd)
        elif name == 'inversion':
            cmd = 'CHAN{0:02d}:CABO:INVE?'.format(axis)
            val = self.sendCmd(cmd)
            if val.lower() == 'off':
                ret = False
            elif val.lower() == 'on':
                ret = True
            return ret
        elif name == 'instantcurrent':
            cmd = 'CHAN{0:02d}:INSCurrent?'.format(axis)
            return eval(self.sendCmd(cmd))

    def SetAxisExtraPar(self, axis, name, value):
        if axis == 1:
            raise ValueError('The axis 1 does not use the extra attributes')

        name = name.lower()
        axis -= 1
        if name == "range":
            cmd = 'CHAN{0:02d}:CABO:RANGE {1}'.format(axis, value)
            self.sendCmd(cmd)
        elif name == 'inversion':
            cmd = 'CHAN{0:02d}:CABO:INVE {1}'.format(axis, int(value))
            self.sendCmd(cmd)


###############################################################################
#                Controller Extra Attribute Methods
###############################################################################

    def SetCtrlPar(self, parameter, value):
        param = parameter.lower()
        if param == 'acquisitionmode':
            self.sendCmd('ACQU:MODE %s' % value)
        else:
            CounterTimerController.SetCtrlPar(self, parameter, value)

    def GetCtrlPar(self, parameter):
        param = parameter.lower()
        if param == 'acquisitionmode':
            value = self.sendCmd('ACQU:MODE?')
        else:
            value = CounterTimerController.GetCtrlPar(self, parameter)
        return value


if __name__ == '__main__':
    host = 'electproto19'
    port = 5025
    ctrl = Albaem2CoTiCtrl('test', {'AlbaEmHost': host, 'Port': port})
    ctrl.AddDevice(1)
    ctrl.AddDevice(2)
    ctrl.AddDevice(3)
    ctrl.AddDevice(4)
    ctrl.AddDevice(5)

    ctrl._synchronization = AcqSynch.SoftwareTrigger
    # ctrl._synchronization = AcqSynch.HardwareTrigger
    acqtime = 1.1
    ctrl.LoadOne(1, acqtime, 10, 0)
    ctrl.StartAllCT()
    t0 = time.time()
    ctrl.StateAll()
    while ctrl.StateOne(1)[0] != State.On:
        ctrl.StateAll()
        time.sleep(0.1)
    print(time.time() - t0 - acqtime)
    ctrl.ReadAll()
    print(ctrl.ReadOne(2))
