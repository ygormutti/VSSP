#!/usr/bin/python
# -*- coding: utf-8 -*-

import vssp
import time

def open_dsp():
    import ossaudiodev as oss
    dsp = oss.open('/dev/dsp', 'w')
    dsp.setparameters(oss.AFMT_S16_LE, 2, 44100)
    return dsp


def main():
    rx = vssp.VSSPReceiver('127.0.0.1', 4242)
    rx.connect()
    dsp = open_dsp()
    while True:
        url = raw_input('URL: ').decode('utf-8')
        stream = rx.request(url)
        time.sleep(1)
        data = stream.read()
        data = data[44:] # pulando o cabeçalho do WAV
        while stream.receiving:
            dsp.write(data)
            try:
                data = stream.read()
            except vssp.MissingSegment, ms:
                print 'Segmento perdido:', ms
        print 'Fim do fluxo "{}"'.format(url.encode('utf-8'))
    dsp.close()


if __name__ == '__main__':
    main()
