#!/usr/bin/python3

#Bark Detector
#v 0.1

import sys, getopt #Für Argumentenliste beim Start
import pyaudio
import wave
from collections import deque
import queue
import audioop
import json
import time
import threading
import os
import logging
import logging.handlers

############################# Logger-Config

logging.basicConfig(level=logging.INFO) # es reicht hier das Level zu ändern !
logger = logging.getLogger()

# Log in Datei
file_handler = logging.handlers.RotatingFileHandler('detector-log.txt', maxBytes=25*1024*1024, backupCount=1)
file_handler.setLevel(logging.DEBUG)
# Ausgabeformat Datei
formatter_file = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(formatter_file)
# Hadler zum root-logger hinzufügen
logger.addHandler(file_handler)

class Recorder(threading.Thread):

    def __init__(self, para, pa, dev_index, dataqueue):
        threading.Thread.__init__(self)
        self.para = para
        self.pa = pa
        self.dev_index = dev_index
        self.buffer_chunk = self.para['sample_rate'] / self.para['buf_size']
        buffer_size = int(self.para['size'] * self.buffer_chunk)
        self.buffer = deque(maxlen=buffer_size)
        self.dataqueue = dataqueue

    def read_stream(self):
        stream = self.pa.open(format=self.para['format'],
                              rate=self.para['sample_rate'],
                              channels=self.para['channels'],
                              input_device_index=self.dev_index,
                              input=True,
                              frames_per_buffer=self.para['buf_size'])

        try:
            part = stream.read(self.para['buf_size'])
        except Exception as ex:
            print('Error reading stream:', ex)
            logger.error('Error reading stream: %s', ex)
            part = None

        stream.stop_stream()
        stream.close()

        return part

    def run(self):

        try:
            secs_remain = (self.para['size'] - self.para['pretrigger'])
            while 1:

                noise = False
                print('listening...')

                while not noise:

                    part = self.read_stream()
                    if part == None:
                        self.buffer.clear()
                    else:
                        rms = audioop.rms(part, 2)                                             #durchschnittliche Lautstärke ermitteln
                        self.buffer.append(audioop.mul(part, 1, self.para['multiplikator']))   # Lautstärke anheben - Rauschen steigt mit!
                        if rms > self.para['trigger']:
                            print('!!! SOUND ALARM !!! Level', rms)
                            noise = True

                print('record still', str(secs_remain), 'seconds...' )
                for i in range(int(secs_remain * self.buffer_chunk)):
                    part = self.read_stream()
                    if part == None:
                        self.buffer.clear()
                    else:
                        self.buffer.append(audioop.mul(part, 1, self.para['multiplikator']))



                if self.buffer:
                    self.dataqueue.put_nowait((self.buffer.copy(), rms))
                self.buffer.clear()

        except Exception as ex:
            print('Recorder Exception:\n',ex)
            logger.error('Recorder Exception: %s', ex)

        #finally:
        #    stream.stop_stream()
        #    stream.close()


class BarkDetector(threading.Thread):

    def __init__(self, para):
        threading.Thread.__init__(self)
        self.para = para
        self.active = True
        self.dataqueue = queue.Queue()
        self.history_dict = {}

        if os.path.isfile('history.json'):
            try:
                with open('history.json', 'r') as file:
                    self.history_dict = json.load(file)
            except Exception as ex:
                print(ex)

    def stop(self):
        self.active = False

    def find_device(self, pa, name):
        target = None
        for i in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(i).get('name')
            if name in info:
                target = i
                break
        return target

    def get_time(self, format):
        if format == 'date':
            return time.strftime('%d-%m-%Y', time.localtime())
        elif format == 'time':
            return time.strftime('%H:%M:%S', time.localtime())
        else:
            return None

    def make_summary(self):
        today = self.get_time(format='date')
        if today in self.history_dict.keys():
            barktext = ''
            for i, barktime in enumerate(sorted(self.history_dict[today].keys(), reverse=True)):
                level = round(self.history_dict[today][barktime]['level'] / 1000, 1)
                barktext += '\n   ' + barktime + '   level: ' + str(level)

            with open('summary.txt', 'w') as sum_file:
                sum_file.write(str(i+1) + ' detected barking, at ' + today)
                sum_file.write('\n')
                sum_file.write(barktext)


    def run(self):
        self.pa = pyaudio.PyAudio()
        dev_index = self.find_device(self.pa, self.para['name'])
        if dev_index == None:
            print('no device found!')
            logger.error('Device %s not found!', self.para['name'])

        else:
            self.recorder = Recorder(self.para, self.pa, dev_index, self.dataqueue)
            self.recorder.daemon = True
            self.recorder.start()

            try:
                while self.active:
                    data, noise_val = self.dataqueue.get() #blockiert bis was kommt

                    datestr = self.get_time(format='date')
                    timestr = self.get_time(format='time')

                    folderpath = './' + datestr + '/'
                    if not os.path.isdir(folderpath):
                        os.mkdir(folderpath)

                    filename = timestr.replace(':','-') + '.wav'

                    print('write ', filename)

                    with wave.open(folderpath + filename, 'wb') as wavefile:
                        wavefile.setnchannels(self.para['channels'])
                        wavefile.setsampwidth(self.pa.get_sample_size(self.para['format']))
                        wavefile.setframerate(self.para['sample_rate'])
                        data.pop()  # Erstes Element weglassen - fehlerhaft! Grund noch unbekannt
                        for i, elem in enumerate(data):
                            wavefile.writeframes(elem)

                    if datestr not in self.history_dict.keys():
                        self.history_dict.update({datestr:{}})
                    self.history_dict[datestr].update({timestr:{'level':noise_val}})

                    with open('history.json', 'w') as file:
                        json.dump(self.history_dict, file, indent=4, sort_keys=True)

                    self.make_summary()

            except Exception as ex:
                print('BarkDetextor Error:\n', ex)
                logger.error('BarkDetextor Error: %s', ex)

            finally:
                self.pa.terminate()


if __name__ == '__main__':

    logger.info('Bark Detector Start!')

    #Argumentenliste

    #   -s  --size              Dateigröße in Sekunden
    #   -m  --multiplikator     Faktor um den die Lautstärke angehoben wird
    #   -n  --name              Name, Bezeichnung (zumindest ein Teil davon) des Aufnahmegerätes
    #   -p  --pretrigger        Sekunden die VOR der Geräuscherkennung zu hören sein sollen
    #   -t  --trigger           Grenzwert für die Geräuscherkennung
    #   -h  --help              Gibt die Liste der gültigen Parameter aus

    helptext = '\nVerfügbare optionen:\n'
    helptext += '\n   -s  --size              Dateigröße in Sekunden (1-20)'
    helptext += '\n   -m  --multiplikator     Faktor um den die Lautstärke angehoben wird (1-15)'
    helptext += '\n   -n  --name              Name, Bezeichnung des Aufnahmegerätes (std: iTalk-02)'
    helptext += '\n   -p  --pretrigger        Sekunden die VOR der Geräuscherkennung zu hören sein sollen(1-10)'
    helptext += '\n   -t  --trigger           Grenzwert für die Geräuscherkennung(0.1-10.9)'
    helptext += '\n   -h  --help              Gibt die Liste der gültigen Parameter aus'
    opts, args = getopt.getopt(sys.argv[1:], 'hs:m:n:p:t:', ['help',
                                                             'size=',
                                                             'multiplikator=',
                                                             'name=',
                                                             'pretrigger=',
                                                             'trigger='])

    para = {'format': pyaudio.paInt16,  # 16-bit resolution
            'channels': 1,
            'sample_rate': 48000,   # 48kHz sampling rate
            'buf_size': 2048,       # 2^x samples for buffer
            'size': 8,              # Aufnahmelänge in Sek
            'trigger': 3000,        # Ab dieser rms-Lautstärke wird aufgezeichnet
            'pretrigger': 4,        # Zeit vor dem Soundalarm in Sek
            'name': 'iTalk-02',     # (Teilweise)Bezeichnung des Aufnahmegerätes
            'multiplikator': 3}     # Faktor für die Lautstärkenanhebung

    for opt, value in opts:
        key = opt.replace('-', '')

        if key in ['s', 'size']:
            if not 0 < int(value) < 21:
                value = 20
            print('set size to', value)
            para.update({'size' : int(value)})

        elif key in ['m', 'multiplikator']:
            if not 0 < int(value) < 16:
                value = 4
            print('set multiplikator to', value)
            para.update({'multiplikator': int(value)})

        elif key in ['n', 'name']:
            print('try using', value)
            para.update({'name': value})

        elif key in ['p', 'pretrigger']:
            print('set pretrigger to', value, 'seconds')
            para.update({'pretrigger': int(value)})

        elif key in ['t', 'trigger']:
            if not 0 < float(value) < 11:
                value = 5
            print('set trigger level to', value)
            para.update({'trigger': int(float(value) * 1000)})

        elif key in ['h', 'help']:
            print(helptext)
            sys.exit(0)

    if para['pretrigger'] >= para['size']:
        new_val = para['size'] // 2
        para['pretrigger'] = new_val
        para.update({'size': int(value)})
        print('wrong pretrigger - set value to', new_val)

    print('starting bark detector')
    for key in para.keys():
        print('   ',key, ' : ', para[key])

    bt = BarkDetector(para)
    bt.daemon = True
    try:
        bt.start()
        bt.join()
    except KeyboardInterrupt:
        print('manually stopped!')
    except Exception as ex:
        print('Error:', ex)
    finally:
        bt.stop()

        time.sleep(3)
        threadlist = threading.enumerate()
        print('still active threads:', threadlist)

        print('Bark Detector End!')
        logger.info('Bark Detector End!')

        sys.exit(0)
