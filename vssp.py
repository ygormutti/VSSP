#!/usr/bin/env python
# -*- coding: utf-8 -*-

from __future__ import print_function
import struct
import socket
import os
import threading
import time

DEBUG = False 

T_REQ = 0
T_END = 1 << 14
T_ETC = 2 << 14
T_DAT = 3 << 14

T_REQ_OPEN_A = T_REQ
T_REQ_OPEN_B = 1 + T_REQ
T_REQ_OK     = 2 + T_REQ
T_REQ_DENIED = 3 + T_REQ
T_REQ_STREAM = 4 + T_REQ

T_END_CONNECTION = T_END
T_END_STREAM     = 1 + T_END

T_ETC_PMTU_PROBE = T_ETC
T_ETC_PMTU_ACK   = 1 + T_ETC

TYPE_BITMASK = 49152
SEQNO_BITMASK = 16383

MAX_PAYLOAD = 16386
MAX_SEQNO = 16384

_formatter = struct.Struct('!H')


def debug(*args, **kwargs):
    if DEBUG:
        print(*args, **kwargs)


def _format_header(type, seqno=None):
    if (type == T_DAT):
        return _formatter.pack(type | seqno)
    return _formatter.pack(type)


def _parse_header(raw):
    bytes = _formatter.unpack(raw)[0]
    type_ = bytes & TYPE_BITMASK
    if type_ == T_DAT:
        return (type_, bytes & SEQNO_BITMASK)
    return (bytes, None)


def _increment_seqno(seqno):
    return (seqno + 1) % MAX_SEQNO


def _decrement_seqno(seqno):
    return (seqno - 1) % MAX_SEQNO


class VSSPError(Exception):
    pass


class MissingSegment(Exception):
    pass


class VSSPStream(threading.Thread):
    def __init__(self, socket, addr, mss):
        super(VSSPStream, self).__init__()
        self.socket = socket
        self.addr = addr
        self.mss = mss
        self.seqno = 0
        self.timestamp = 0
        self.buffer = ''
        self.window = {}
        self.buffer_lock = threading.Lock()
        self.receiving = True

    def read(self):
        debug('Aplicação solicitou dados do fluxo. Adquirindo trava...')
        with self.buffer_lock:
            debug('Trava adquirida! Lendo dados do buffer.')
            data = self.buffer[:self.mss]
            if len(data) == 0 and self.receiving and \
                len(self.window) > 0 and \
                not self.window[self.seqno][0] and \
                self.window[self.seqno][2] < time.time():
                prev_seqno = self.seqno
                self.seqno = _increment_seqno(self.seqno)
                raise MissingSegment(prev_seqno)
            self.buffer = self.buffer[self.mss:]
            return data

    def _append_buffer(self, data):
        debug('Movendo segmentos contíguos para buffer. Adquirindo trava...')
        with self.buffer_lock:
            debug('Trava adquirida! Escrevendo dados no buffer.')
            self.buffer += data

    def _store_packet(self, seqno, data):
        debug('Pacote', seqno, 'recebido e adicionado na janela de recepção.')
        self.window[seqno] = (True, data, time.time())

    def _insert_placeholder(self, seqno):
        debug('O pacote', seqno, 'está faltando. Iniciando temporizador.')
        self.window[seqno] = (False, None, time.time() + self._timeout)

    def _receive(self):
        addr = None
        while addr != self.addr:
            data, addr = self.socket.recvfrom(self.mss)
        return (_parse_header(data[:2]) + (data[2:],))

    @property
    def _next_seqno(self):
        seqno = self.seqno
        while True:
            seqno = _increment_seqno(seqno)
            if seqno not in self.window or self.window[seqno][2] < self.timestamp:
                return _decrement_seqno(seqno)
            if self.window[seqno][0]:
                return seqno

    @property
    def _timeout(self):
        return 1

    def run(self):
        while True:
            type_, seqno, data = self._receive()
            if type_ == T_END_STREAM:
                debug('T_END_STREAM recebido')
                self.receiving = False
                break
            elif type_ == T_DAT:
                self._store_packet(seqno, data)
                i = self.seqno
                while i != seqno:
                    if i not in self.window or self.window[i][2] < self.timestamp:
                        self._insert_placeholder(i)
                        i = _increment_seqno(i)
                while seqno == self.seqno:
                    self._append_buffer(self.window[seqno][1])
                    (received, data, timestamp) = self.window[seqno]
                    self.window[seqno] = (received, None, timestamp)
                    self.timestamp = timestamp
                    seqno = self._next_seqno
                    self.seqno = _increment_seqno(self.seqno)


class VSSPReceiver(object):
    def __init__(self, host, port):
        (family, socktype, proto, canonname, sockaddr) = \
        socket.getaddrinfo(host, port, socket.AF_UNSPEC, socket.SOCK_DGRAM)[0]
        self.addr = sockaddr
        self.socket = socket.socket(family, socktype, proto)
        self.socket.connect(self.addr)
        self.mss = MAX_PAYLOAD

    def _send(self, type_, data=''):
        self.socket.send(_format_header(type_) + data)

    def _receive(self):
        addr = None
        while addr != self.addr:
            data, addr = self.socket.recvfrom(self.mss)
        return (data[:2], data[2:])

    def connect(self):
        debug('Enviando T_REQ_OPEN_A')
        self._send(T_REQ_OPEN_A)
        hdr, data = self._receive()
        if _parse_header(hdr)[0] != T_REQ_OK:
            raise VSSPError('O transmissor recusou o pedido de conexão.')
        debug('T_REQ_OK recebido, conexão aberta')
        while True:
            rawhdr, data = self._receive()
            type_ = _parse_header(rawhdr)[0]
            if type_ == T_ETC_PMTU_PROBE:
                debug('T_ETC_PMTU_PROBE recebido, enviando T_ETC_PMTU_ACK')
                self._send(_format_header(T_ETC_PMTU_ACK),
                           _formatter.pack(len(data)))
            elif type_ == T_ETC_PMTU_ACK:
                self.mss = _formatter.unpack(data)[0]
                debug('T_ETC_PMTU_ACK recebido, o MSS é', self.mss)
                self._send(T_ETC_PMTU_ACK, data)
                debug('Enviando T_ETC_PMTU_ACK')
                break

    def request(self, url):
        if not isinstance(url, unicode):
            raise ValueError('URLs devem ser do tipo unicode')
        debug('Enviando REQ_STREAM')
        self._send(T_REQ_STREAM, url.encode('utf-8'))
        stream = VSSPStream(self.socket, self.addr, self.mss)
        stream.start()
        return stream

class VSSPTransmitter(object):
    def __init__(self, port, mss=1450):
        (family, socktype, proto, canonname, sockaddr) = \
        socket.getaddrinfo(None, port, socket.AF_UNSPEC, socket.SOCK_DGRAM,
                           socket.IPPROTO_UDP, socket.AI_PASSIVE)[0]
        self.addr = sockaddr
        self.socket = socket.socket(family, socktype, proto)
        self.socket.bind(self.addr)
        self.mss = mss

    def _send(self, header, data=''):
        self.socket.sendto(_format_header(*header) + data, self.addr)

    def _receive(self):
        addr = None
        while addr != self.addr:
            data, addr = self.socket.recvfrom(self.mss)
        return (data[:2], data[2:])

    def listen(self):
        debug('Esperando T_REQ_OPEN_A')
        while True:
            data, addr = self.socket.recvfrom(self.mss)
            type_ = _parse_header(data[:2])[0]
            if type_ == T_REQ_OPEN_A:
                debug('T_REQ_OPEN_A recebido de', addr)
                return addr
            debug('Enviando T_REQ_DENIED')
            self.socket.sendto(_format_header(T_REQ_DENIED), addr)

    def accept(self, addr):
        debug('Enviando T_REQ_OK')
        self.addr = addr
        self._send((T_REQ_OK,))
        debug('Enviando T_ETC_PMTU_ACK')
        self._send((T_ETC_PMTU_ACK,), _formatter.pack(1450))
        rawhdr, data = self._receive()
        if _parse_header(rawhdr)[0] == T_ETC_PMTU_ACK:
            self.mss = _formatter.unpack(data)[0]
            debug('T_ETC_PMTU_ACK recebido, confirmado MSS de', self.mss)

    def handle_request(self, interval=0):
        url = None
        while True:
            debug('Esperando T_REQ_STREAM')
            rawhdr, data = self._receive()
            header = _parse_header(rawhdr)
            if header[0] == T_REQ_STREAM:
                debug('T_REQ_STREAM recebido para o url', data)
                url = data.decode('utf-8')
                break
        stream = open(url, 'r')
        seqno = 0
        segment = stream.read(self.mss - 2)
        while segment:
            self._send((T_DAT, seqno), segment)
            time.sleep(interval)
            seqno += 1
            segment = stream.read(self.mss - 2)
        self._send((T_END_STREAM,))
