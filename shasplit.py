#!/usr/bin/env python

'''Shasplit - SHA-based data splitting for efficient backups'''

__copyright__ = '''\
Copyright (C) 2012  Volker Grabsch <vog@notjusthosting.com>

Permission to use, copy, modify, and/or distribute this software for any
purpose with or without fee is hereby granted, provided that the above
copyright notice and this permission notice appear in all copies.

THE SOFTWARE IS PROVIDED "AS IS" AND THE AUTHOR DISCLAIMS ALL WARRANTIES
WITH REGARD TO THIS SOFTWARE INCLUDING ALL IMPLIED WARRANTIES OF
MERCHANTABILITY AND FITNESS. IN NO EVENT SHALL THE AUTHOR BE LIABLE FOR
ANY SPECIAL, DIRECT, INDIRECT, OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES
WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN AN
ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION, ARISING OUT OF
OR IN CONNECTION WITH THE USE OR PERFORMANCE OF THIS SOFTWARE.
'''

import argparse
import hashlib
import logging
import os
import os.path
import sys
import uuid

def check_name(name):
    if len(name) < 1:
        raise TypeError('Name must not be empty')
    if os.path.dirname(name) != '':
        raise TypeError(r'Name must not have a directory component')
    if name[0] in '._':
        raise TypeError('Name must not start with "." or "_"')

def check_maxbackups(maxbackups):
    if not (maxbackups > 0):
        raise TypeError('Maximum number of backups must be positive')

def check_volumegroup(volumegroup):
    if len(volumegroup) < 1:
        raise TypeError('Volume group must not be empty')
    if os.path.dirname(volumegroup) != '':
        raise TypeError(r'Volume group must not have a directory component')
    if volumegroup[0] == '.':
        raise TypeError('Volume group must not start with "."')

def check_blocksize(blocksize):
    if not (blocksize > 0):
        raise TypeError('Block size must be positive')

def shasplit(input_io, name, outputdir, blocksize, algorithm):
    '''Split data from input_io into hashed parts'''
    check_name(name)
    check_blocksize(name)
    hash_total = hashlib.new(algorithm)
    if not os.path.exists(outputdir):
        raise RuntimeError('Directory %r does not exist' % (outputdir,))
    datadir = os.path.join(outputdir, '_data')
    namedir = os.path.join(outputdir, name)
    if os.path.exists(namedir):
        raise RuntimeError('Directory %r already exists' % (namedir,))
    namedir_tmp = os.path.join(outputdir, '_tmp_' + str(uuid.uuid4()))
    os.mkdir(namedir_tmp)
    if not os.path.exists(datadir):
        os.mkdir(datadir)
    part_start = 1
    part_len = 5
    for partnr in xrange(part_start, 10**part_len + 1):
        part = str(partnr).rjust(part_len, '0')
        if len(part) != part_len:
            raise RuntimeError('Too many parts')
        block = input_io.read(blocksize)
        if len(block) == 0:
            break
        hexdigest = hashlib.new(algorithm, block).hexdigest()
        hash_total.update(block)
        logging.debug('Part %s: Creating symlink', part)
        part_filename = os.path.join(namedir_tmp, 'part_' + part)
        part_filename_tmp = os.path.join(outputdir, '_tmp_' + str(uuid.uuid4()))
        os.symlink(os.path.join('..', os.path.basename(datadir), hexdigest), part_filename_tmp)
        os.rename(part_filename_tmp, part_filename)
        data_filename = os.path.join(datadir, hexdigest)
        if os.path.exists(data_filename):
            logging.debug('Part %s: Skipping existing data file %r', part, data_filename)
        else:
            logging.debug('Part %s: Creating data file %r', part, data_filename)
            data_filename_tmp = os.path.join(outputdir, '_tmp_' + str(uuid.uuid4()))
            with open(data_filename_tmp, 'wb') as f:
                f.write(block)
            os.rename(data_filename_tmp, data_filename)
    hexdigest_total = hash_total.hexdigest()
    logging.debug('Creating hash file containing %s', hexdigest_total)
    hash_filename = os.path.join(namedir_tmp, 'hash')
    hash_filename_tmp = os.path.join(outputdir, '_tmp_' + str(uuid.uuid4()))
    with open(hash_filename_tmp, 'wb') as f:
        f.write(hexdigest_total + '\n')
    os.rename(hash_filename_tmp, hash_filename)
    os.rename(namedir_tmp, namedir)

def clean(outputdir):
    raise NotImplementedError()

def check(outputdir, algorithm):
    raise NotImplementedError()

def backup(maxbackups, do_clean, input_io, name, outputdir, blocksize, algorithm):
    if maxbackups is not None:
        check_maxbackups(maxbackups)
        raise NotImplementedError()
    shasplit(input_io, name, outputdir, blocksize, algorithm)
    if do_clean:
        clean(outputdir)

def backup_lvm(volumegroup, maxbackups, do_clean, name, outputdir, blocksize, algorithm):
    check_volumegroup(volumegroup)
    raise NotImplementedError()

def main():
    '''Run command line tool'''
    def name(s):
        check_name(s)
        return s
    def maxbackups(s):
        i = int(s)
        check_maxbackups(i)
        return i
    def volumegroup(s):
        check_volumegroup(s)
        return s
    def blocksize(s):
        i = int(s)
        check_blocksize(i)
        return i
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', '--name', type=name, help='split data into the named directory')
    parser.add_argument('-m', '--maxbackups', type=maxbackups, help='maximum number of backups to keep')
    parser.add_argument('-g', '--volumegroup', type=volumegroup, help='volume group for automatic LVM snapshot')
    parser.add_argument('-c', '--clean', action='store_true', help='remove orphaned data parts and old temporary files')
    parser.add_argument('-x', '--check', action='store_true', help='check output directory for consistency')
    parser.add_argument('-o', '--outputdir', default='.', help='base output directory')
    parser.add_argument('-b', '--blocksize', default=64*1024, type=blocksize, help='set block size')
    parser.add_argument('-a', '--algorithm', default='sha1', choices=hashlib.algorithms, help='set hash algorithm')
    parser.add_argument('-v', '--verbose', action='store_true', help='enable verbose output')
    args = parser.parse_args()
    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(message)s')
    else:
        logging.basicConfig(level=logging.INFO, format='%(message)s')
    logging.debug('Received arguments %r', args)
    if args.check:
        if not check(args.outputdir, args.algorithm):
            sys.exit(1)
    if args.name is not None and args.volumegroup is not None:
        backup_lvm(args.volumegroup, args.maxbackups, args.clean, args.name, args.outputdir, args.blocksize, args.algorithm)
        sys.exit(0)
    if args.name is not None and args.volumegroup is None:
        logging.info('Reading from stdin')
        backup(args.maxbackups, args.clean, sys.stdin, args.name, args.outputdir, args.blocksize, args.algorithm)
        sys.exit(0)
    if args.clean:
        clean(args.outputdir)
        sys.exit(0)
    if args.check:
        # The check has already been executed successfully
        sys.exit(0)
    parser.print_help()
    sys.exit(1)

if __name__ == '__main__':
    main()
