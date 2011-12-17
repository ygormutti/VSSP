#!/usr/bin/python
# -*- coding: utf-8 -*-

import vssp


def main():
    tx = vssp.VSSPTransmitter(4242, 1448)
    tx.accept(tx.listen())
    while True:
        tx.handle_request(0.004)


if __name__ == '__main__':
    main()
