#!/usr/bin/env python

'''Shasplit - SHA-based data splitting for efficient backups'''

__copyright__ = '''\
Copyright (C) 2012, 2013  Volker Grabsch <v@njh.eu>

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

import datetime
import hashlib
import itertools
import logging
import os
import os.path
import re
import sys

class Shasplit:

    def __init__(self, algorithm='sha1', partsize=1024*1024, maxparts=1000000, directory='~/.shasplit'):
        self.algorithm = self.validate_algorithm(algorithm)
        self.partsize = self.validate_partsize(partsize)
        self.maxparts = self.validate_maxparts(maxparts)
        self.directory = os.path.expanduser(directory)

    def hash_filename(self, hexdigest):
        return os.path.join('.data', hexdigest[:3], hexdigest[3:])

    def namedir(self, name):
        return os.path.join(self.directory, name)

    def names(self):
        for name in os.listdir(self.directory):
            if name != '.data':
                yield name

    def instancedir(self, name, timestamp):
        return os.path.join(self.namedir(name), timestamp.replace(':', ''))

    def requiredfiles(self, name, timestamp):
        instancedir = self.instancedir(name, timestamp)
        for requiredfile_name in ['size', 'hash']:
            yield os.path.join(instancedir, requiredfile_name)

    def timestamps(self, name):
        for timestampdir in os.listdir(self.namedir(name)):
            timestamp = timestampdir[:13] + ':' + timestampdir[13:15] + ':' + timestampdir[15:]
            size, expected_size = self.sizes(name, timestamp)
            yield timestamp, size, expected_size

    def partfiles(self, name, timestamp):
        instancedir = self.instancedir(name, timestamp)
        for partdir_name in sorted(os.listdir(instancedir)):
            partdir = os.path.join(instancedir, partdir_name)
            if os.path.isdir(partdir):
                for partfile in sorted(os.listdir(partdir)):
                    yield os.path.join(partdir, partfile)

    def sizes(self, name, timestamp):
        size = 0
        for partfile in self.partfiles(name, timestamp):
            partsize = os.path.getsize(partfile)
            logging.debug('Size of %r is %r', partfile, partsize)
            size += partsize
        for requiredfile in self.requiredfiles(name, timestamp):
            if not os.path.exists(requiredfile):
                logging.debug('Missing required file %r', requiredfile)
                expected_size = None
                return size, expected_size
        instancedir = self.instancedir(name, timestamp)
        with open(os.path.join(instancedir, 'size'), 'rb') as f:
            expected_size = int(f.read())
        if expected_size < 0:
            raise ValueError('Negative expected size: %r' % (expected_size,))
        if size > expected_size:
            raise RuntimeError('Integrity error: expected size %r, actual size %r' % (expected_size, size))
        return size, expected_size

    def write_file(self, filename, data):
        filedir = os.path.dirname(filename)
        if not os.path.exists(filedir):
            logging.debug('Creating directory %r', filedir)
            os.makedirs(filedir)
        logging.debug('Creating file %r of size %r', filename, len(data))
        with open(filename, 'wb') as f:
            f.write(data)

    def symlink(self, target, filename):
        filedir = os.path.dirname(filename)
        if not os.path.exists(filedir):
            logging.debug('Creating directory %r', filedir)
            os.makedirs(filedir)
        logging.debug('Creating symlink %r -> %r', filename, target)
        os.symlink(target, filename)

    def remove_obsolete(self, name, maxbackups):
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        logging.debug('Removing obsolete backups of %r while keeping at most %r backups', name, maxbackups)
        # TODO: Implement this

    def add(self, name, maxbackups, input_io):
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        logging.debug('Adding to %r while keeping at most %r backups', name, maxbackups)
        self.remove_obsolete(name, maxbackups)
        timestamp = self.validate_timestamp(datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
        instancedir = self.instancedir(name, timestamp)
        if os.path.exists(instancedir):
            raise RuntimeError('Directory already exists: %r' % (instancedir,))
        hash_total = hashlib.new(self.algorithm)
        size_total = 0
        for partnr in itertools.count(0):
            data = input_io.read(self.partsize)
            if len(data) == 0:
                break
            hash_total.update(data)
            size_total += len(data)
            if partnr >= self.maxparts:
                raise RuntimeError('Too many parts')
            dirlen = 3
            partlen = max(dirlen + 1, len(str(self.maxparts - 1)))
            part = str(partnr).rjust(partlen, '0')
            hexdigest = hashlib.new(self.algorithm, data).hexdigest()
            target = os.path.join('..', '..', '..', self.hash_filename(hexdigest))
            symlink_filename = os.path.join(instancedir, part[:dirlen], part[dirlen:])
            self.symlink(target, symlink_filename)
            data_filename = os.path.join(self.directory, self.hash_filename(hexdigest))
            if os.path.exists(data_filename) and os.path.getsize(data_filename) == len(data):
                logging.debug('Skipping existing complete data file %r', data_filename)
            else:
                self.write_file(data_filename, data)
        self.write_file(os.path.join(instancedir, 'hash'), '%s\n' % (hash_total.hexdigest(),))
        self.write_file(os.path.join(instancedir, 'size'), '%d\n' % (size_total,))
        self.remove_obsolete(name, maxbackups)

    def add_lvm(self, volumegroup, name, maxbackups, input_io):
        volumegroup = self.validate_volumegroup(volumegroup)
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        raise NotImplementedError()

    def status(self):
        for name in sorted(self.names()):
            logging.info('%s', name)
            for timestamp, size, expected_size in sorted(self.timestamps(name), reverse=True):
                if size == expected_size:
                    completeness = ''
                else:
                    completeness = '  incomplete'
                if expected_size is None:
                    expected_size_s = '(unknown)'
                    percentage = 0
                elif expected_size == 0:
                    expected_size_s = str(expected_size)
                    percentage = 100
                else:
                    expected_size_s = str(expected_size)
                    percentage = int(100 * size / expected_size)
                logging.info('  %s  %s  %3d%%%s', timestamp, expected_size_s, percentage, completeness)

    def check(self):
        raise NotImplementedError()

    def recover_nosizecheck(self, name, timestamp, output_io):
        name = self.validate_name(name)
        timestamp = self.validate_timestamp(timestamp)
        logging.debug('Recovering %r at %r', name, timestamp)
        hash_total = hashlib.new(self.algorithm)
        for partfile in self.partfiles(name, timestamp):
            logging.debug('Reading from %r', partfile)
            with open(partfile, 'rb') as f:
                data = f.read()
            output_io.write(data)
            hash_total.update(data)
        with open(os.path.join(self.instancedir(name, timestamp), 'hash'), 'rb') as f:
            expected_hash = f.read().rstrip('\n')
        if hash_total.hexdigest() != expected_hash:
            raise RuntimeError('Integrity error: expected hash %r, actual hash %r' % (expected_hash, hash_total.hexdigest()))

    def recover(self, name, timestamp, output_io):
        name = self.validate_name(name)
        timestamp = self.validate_timestamp(timestamp)
        size, expected_size = self.sizes(name, timestamp)
        if size != expected_size:
            raise RuntimeError('Incomplete backup: expected size %r, actual size %r' % (expected_size, size))
        self.recover_nosizecheck(name, timestamp, output_io)

    def recover_latest(self, name, output_io):
        name = self.validate_name(name)
        completed = [timestamp for timestamp, size, expected_size in self.timestamps(name) if size == expected_size]
        if len(completed) == 0:
            raise RuntimeError('No completed backup available')
        self.recover_nosizecheck(name, max(completed), output_io)

    def validate_algorithm(self, algorithm):
        algorithm = str(algorithm)
        if algorithm not in hashlib.algorithms:
            raise TypeError('Unknown secure hash algorithm')
        return algorithm

    def validate_maxbackups(self, maxbackups):
        maxbackups = int(maxbackups)
        if not (maxbackups > 0):
            raise TypeError('Maximum number of backups must be positive')
        return maxbackups

    def validate_maxparts(self, maxparts):
        maxparts = int(maxparts)
        if not (maxparts > 0):
            raise TypeError('Maximum number of parts must be positive')
        return maxparts

    def validate_name(self, name):
        name = str(name)
        if len(name) < 1:
            raise TypeError('Name must not be empty')
        if os.path.dirname(name) != '':
            raise TypeError('Name must not have a directory component')
        if name[0] in '._':
            raise TypeError('Name must not start with "." or "_"')
        return name

    def validate_partsize(self, partsize):
        partsize = int(partsize)
        if not (partsize > 0):
            raise TypeError('Part size must be positive')
        return partsize

    def validate_timestamp(self, timestamp):
        timestamp = str(timestamp)
        if not re.match('^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$', timestamp):
            raise TypeError('Timestamp must have format YYYY-MM-DDThh:mm:ss')
        return timestamp

    def validate_volumegroup(self, volumegroup):
        volumegroup = str(volumegroup)
        if len(volumegroup) < 1:
            raise TypeError('Volume group must not be empty')
        if os.path.dirname(volumegroup) != '':
            raise TypeError('Volume group must not have a directory component')
        if volumegroup[0] == '.':
            raise TypeError('Volume group must not start with "."')
        return volumegroup

def main():
    '''Run command line tool'''
    shasplit = Shasplit()
    commands = {
        ('add', 2): (shasplit.add, [sys.stdin]),
        ('add', 3): (shasplit.add_lvm, [sys.stdin]),
        ('check', 0): (shasplit.check, []),
        ('status', 0): (shasplit.status, []),
        ('recover', 1): (shasplit.recover_latest, [sys.stdout]),
        ('recover', 2): (shasplit.recover, [sys.stdout]),
    }
    if os.getenv('SHASPLIT_DEBUG') in (None, '', '0'):
        logging.basicConfig(level=logging.INFO, format='%(message)s')
    else:
        logging.basicConfig(level=logging.DEBUG, format='%(levelname)s:%(message)s')
    logging.debug('Received arguments %r', sys.argv)
    if len(sys.argv) <= 1:
        logging.info('Usage: shasplit %s [...]', '|'.join(sorted(set(cmd for (cmd, _) in commands))))
        sys.exit(1)
    cmd = sys.argv[1]
    args = sys.argv[2:]
    try:
        func, extra_args = commands[cmd, len(args)]
    except KeyError, e:
        raise ValueError('Invalid arguments')
    if sys.stdin in extra_args:
        logging.info('Reading from stdin')
    func(*(args + extra_args))

if __name__ == '__main__':
    main()
