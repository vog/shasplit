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
import subprocess
import sys

class Util:

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

    def run_command(self, args):
        logging.debug('Running command %r', args)
        try:
            proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            out, _ = proc.communicate()
            if proc.returncode != 0:
                raise RuntimeError('Command %r failed:\n%s' % (' '.join(args), out.strip()))
        except OSError, e:
            raise RuntimeError('Command %r failed: %s' % (' '.join(args), e))

    def sync(self):
        self.run_command(['sync'])

    def lvcreate(self, volumegroup, name, snapshot, snapshotsize):
        logging.debug('Creating snapshot %r/%r', volumegroup, snapshot)
        self.run_command(['lvcreate', '-s', volumegroup + '/' + name, '-n', snapshot, '-L', str(snapshotsize) + 'B'])

    def lvremove(self, volumegroup, name):
        logging.debug('Removing logical volume %r/%r', volumegroup, name)
        self.run_command(['lvremove', '-f', volumegroup + '/' + name])

class Shasplit:

    def __init__(self, algorithm='sha1', partsize=1*1024*1024, maxparts=1000000, directory='~/.shasplit',
                 snapshotsuffix='_shasplit', snapshotsize=128*1024*1024):
        self.util = Util()
        self.algorithm = self.validate_algorithm(algorithm)
        self.partsize = self.validate_partsize(partsize)
        self.maxparts = self.validate_maxparts(maxparts)
        self.snapshotsuffix = self.validate_snapshotsuffix(snapshotsuffix)
        self.snapshotsize = self.validate_snapshotsize(snapshotsize)
        self.directory = os.path.expanduser(directory)

    def hash_filename(self, hexdigest):
        return os.path.join('.data', hexdigest[:3], hexdigest[3:])

    def hash_of_filename(self, filename):
        datadir, hash1, hash2 = filename.rsplit(os.path.sep, 2)
        return hash1 + hash2

    def namedir(self, name):
        return os.path.join(self.directory, name)

    def names(self):
        if os.path.exists(self.directory):
            for name in os.listdir(self.directory):
                if name != '.data':
                    yield self.validate_name(name)

    def instancedir(self, name, timestamp):
        return os.path.join(self.namedir(name), timestamp.replace(':', ''))

    def requiredfiles(self, name, timestamp):
        instancedir = self.instancedir(name, timestamp)
        for requiredfile_name in ['size', 'hash']:
            yield os.path.join(instancedir, requiredfile_name)

    def timestamps(self, name):
        namedir = self.namedir(name)
        if not os.path.exists(namedir):
            return []
        timestamps = [
            timestampdir[:13] + ':' + timestampdir[13:15] + ':' + timestampdir[15:]
            for timestampdir in os.listdir(namedir)
        ]
        timestamps.sort(reverse=True)
        return timestamps

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

    def remove_obsolete(self, name, maxbackups):
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        logging.debug('Removing obsolete backups of %r while keeping at most %r backups', name, maxbackups)
        # Find all obsolete instances
        instances = []
        num_completed = 0
        for timestamp in self.timestamps(name):
            if num_completed < maxbackups:
                size, expected_size = self.sizes(name, timestamp)
                if size == expected_size:
                    num_completed += 1
            else:
                instances.append((name, timestamp))
        # Remove symlinks and instance directories, remember hashes of used data files
        hexdigests = set()
        for name, timestamp in instances:
            logging.debug('Removing obsolete backup of %r at %r', name, timestamp)
            partdirs = set()
            for partfile in self.partfiles(name, timestamp):
                hexdigest = self.hash_of_filename(os.readlink(partfile))
                hexdigests.add(hexdigest)
                partdirs.add(os.path.dirname(partfile))
                os.remove(partfile)
            for partdir in partdirs:
                os.rmdir(partdir)
            for requiredfile in self.requiredfiles(name, timestamp):
                if os.path.exists(requiredfile):
                    os.remove(requiredfile)
            os.rmdir(self.instancedir(name, timestamp))
        # Check which data files are still used
        for name in self.names():
            for timestamp in self.timestamps(name):
                for partfile in self.partfiles(name, timestamp):
                    hexdigest = self.hash_of_filename(os.readlink(partfile))
                    hexdigests.discard(hexdigest)
        # Remove unused data files
        rel_data_dirnames = set()
        for hexdigest in hexdigests:
            rel_data_filename = self.hash_filename(hexdigest)
            rel_data_dirnames.add(os.path.dirname(rel_data_filename))
            data_filename = os.path.join(self.directory, rel_data_filename)
            if os.path.exists(data_filename):
                logging.debug('Removing data file %r', data_filename)
                os.remove(data_filename)
        # Remove empty data directories
        for rel_data_dirname in rel_data_dirnames:
            data_dirname = os.path.join(self.directory, rel_data_dirname)
            try:
                os.rmdir(data_dirname)
                logging.debug('Removed empty data directory %r', data_dirname)
            except OSError, e:
                pass

    def add_nomaxbackups(self, name, input_io):
        name = self.validate_name(name)
        logging.debug('Adding to %r', name)
        timestamp = self.validate_timestamp(datetime.datetime.now().strftime('%Y-%m-%dT%H:%M:%S'))
        instancedir = self.instancedir(name, timestamp)
        if os.path.exists(instancedir):
            raise RuntimeError('Directory already exists: %r' % (instancedir,))
        if input_io == sys.stdin:
            logging.info('Reading from stdin')
        else:
            logging.debug('Reading from %r', input_io.name)
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
            self.util.symlink(target, symlink_filename)
            data_filename = os.path.join(self.directory, self.hash_filename(hexdigest))
            if os.path.exists(data_filename) and os.path.getsize(data_filename) == len(data):
                logging.debug('Skipping existing complete data file %r', data_filename)
            else:
                self.util.write_file(data_filename, data)
        self.util.write_file(os.path.join(instancedir, 'hash'), '%s\n' % (hash_total.hexdigest(),))
        self.util.write_file(os.path.join(instancedir, 'size'), '%d\n' % (size_total,))

    def add(self, name, maxbackups, input_io):
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        self.remove_obsolete(name, maxbackups)
        self.add_nomaxbackups(name)
        self.remove_obsolete(name, maxbackups)

    def add_lvm(self, volumegroup, name, maxbackups):
        volumegroup = self.validate_volumegroup(volumegroup)
        name = self.validate_name(name)
        maxbackups = self.validate_maxbackups(maxbackups)
        self.remove_obsolete(name, maxbackups)
        self.util.sync()
        snapshot = name + self.snapshotsuffix
        self.util.lvcreate(volumegroup, name, snapshot, self.snapshotsize)
        try:
            with open(os.path.join('/dev', volumegroup, snapshot), 'rb') as input_io:
                self.add_nomaxbackups(name, input_io)
        finally:
            self.util.lvremove(volumegroup, snapshot)
        self.remove_obsolete(name, maxbackups)

    def status(self):
        for name in sorted(self.names()):
            logging.info('%s', name)
            for timestamp in self.timestamps(name):
                size, expected_size = self.sizes(name, timestamp)
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
        completed = []
        for timestamp in self.timestamps(name):
            size, expected_size = self.sizes(name, timestamp)
            if size == expected_size:
                self.recover_nosizecheck(name, timestamp, output_io)
                return
        raise RuntimeError('No completed backup available')

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
        if name[0] in '._-':
            raise TypeError('Name must not start with ".", "_" or "-"')
        if name.endswith(self.snapshotsuffix):
            raise TypeError('Name must not end with %r' % (self.snapshotsuffix,))
        return name

    def validate_partsize(self, partsize):
        partsize = int(partsize)
        if not (partsize > 0):
            raise TypeError('Part size must be positive')
        return partsize

    def validate_snapshotsize(self, snapshotsize):
        snapshotsize = int(snapshotsize)
        if not (snapshotsize > 0):
            raise TypeError('Snapshot size must be positive')
        return snapshotsize

    def validate_snapshotsuffix(self, snapshotsuffix):
        snapshotsuffix = str(snapshotsuffix)
        if len(snapshotsuffix) < 1:
            raise TypeError('Snapshot suffix must not be empty')
        if os.path.dirname(snapshotsuffix) != '':
            raise TypeError('Snapshot suffix must not have a directory component')
        return snapshotsuffix

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
        if volumegroup[0] in '.-':
            raise TypeError('Volume group must not start with "." or "-"')
        return volumegroup

def main():
    '''Run command line tool'''
    shasplit = Shasplit()
    commands = {
        ('add', 2): (shasplit.add, [sys.stdin]),
        ('add', 3): (shasplit.add_lvm, []),
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
    func(*(args + extra_args))

if __name__ == '__main__':
    main()
