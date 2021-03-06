#! /usr/bin/python
# encoding:utf-8

import paramiko
import re
import os


stat_file_config = {
    'cpu': '/proc/stat',
    'net': '/proc/net/dev',
    'io': '/proc/diskstats',
    'mem': '/proc/meminfo',
    'sys': '/proc/stat',
    'vm': '/proc/vmstat',
    'load': '/proc/loadavg',
    'uptime': '/proc/uptime',
    'tcp': '/proc/net/tcp',
    'tcp6': '/proc/net/tcp6'
}

IGNORE_FS = set(['binfmt_misc', 'cgroup', 'debugfs', 'devpts', 'devtmpfs',
                 'fusectl', 'proc', 'pstore', 'securityfs',
                 'sysfs', 'tmpfs', 'xenfs', 'iso9660'])



import time

def format_stat(label, stat_vals):
    ret = {}
    for i in range(len(label)):
        if ':' in label[i]:
            label_n, idx = label[i].split(':')
            idx = int(idx)
        else:
            label_n, idx = label[i], i

        ret[label_n] = round(stat_vals[i], 2)
    return ret


class LinuxStat(object):
    def __init__(self,host,user,password):
        self.host = host
        self.user = user
        self.password = password
        self.curr_stat = {}
        self.stat = {}
        self.last_time = time.time()
        self.loop_cnt = 0
        self.old_stat = {
            'cpu': tuple(0 for _ in xrange(5)),
            'io': tuple(0 for _ in xrange(11)),
            'sys': tuple(0 for _ in xrange(6)),
            'vm': tuple(0 for _ in xrange(6))
        }

    def get_linux(self):
        # 初始化网卡流量数据
        net_nics = self.get_net_nics()
        for nic in net_nics:
            self.old_stat['net_' + nic] = (0,0)
        # 第一次状态采集
        stat1 = self.get_linux_stat()
        # 第二次状态采集
        stat2 = self.get_linux_stat()
        return stat2

    def get_linux_stat(self):
        curr_time = time.time()
        if self.loop_cnt == 0:
            elapsed = self.get_uptime()
        else:
            elapsed = curr_time - self.last_time

        #get all status
        linux_stat = {}
        linux_stat['load'] = self.get_load()
        linux_stat['cpu'] = self.get_cpu_stat()
        linux_stat['iostat'] = self.get_io_stat(elapsed)
        linux_stat['mem'] = self.get_mem_stat()
        linux_stat['vmstat'] = self.get_vm_stat(elapsed)
        linux_stat['tcpstat'] = self.get_tcp_conn_stat()
        linux_stat['net'] = self.get_net_stat(elapsed)

        # update timestamp
        self.last_time = curr_time
        self.loop_cnt += 1
        return linux_stat


    def get_cpu_stat(self):
        #usr, sys, idle, iowait, steal
        stat_name = 'cpu'
        for l in self.get_stat(stat_name):
            if l[0] == 'cpu' and len(l) >= 9:
                self.curr_stat[stat_name] = (long(l[1]) + long(l[2]) + long(l[6]) + long(l[7]),
                                             long(l[3]), long(l[4]), long(l[5]), long(l[8]))

        stat_old = self.old_stat[stat_name]
        stat_curr = self.curr_stat[stat_name]

        delta = (sum(stat_curr) - sum(stat_old)) * 1.0
        if delta > 0:
            self.stat[stat_name] = tuple(100.0 * (stat_curr[i] - stat_old[i])/delta for i in xrange(5))
        else:
            self.stat[stat_name] = tuple(0 for _ in xrange(5))

        self.old_stat[stat_name] = stat_curr

        label = ('user', 'sys', 'idle', 'iowait')
        return format_stat(label, self.stat[stat_name])

    def get_vm_stat(self, elapsed):
        stat_name = 'vm'
        stats = {'pgpgin':0, 'pgpgout':1, 'pswpin':2, 'pswpout':3, 'pgfault':4, 'pgmajfault':5 }
        vm_stat = [0 for _ in xrange(6)]
        for l in self.get_stat(stat_name):
            if l[0] in stats:
                vm_stat[stats[l[0]]] = long(l[1])

        stat_old = self.old_stat[stat_name]

        self.stat[stat_name] = tuple((vm_stat[i] - stat_old[i])/elapsed for i in xrange(len(vm_stat)))
        self.old_stat[stat_name] = vm_stat

        label = ('pgin', 'pgout', 'swapin', 'swapout', 'pgfault', 'pgmajfault')
        return format_stat(label, self.stat[stat_name])

    def get_mem_stat(self):
        stat_name = 'mem'
        stats = {
            'MemTotal':0,
            'MemFree':1,
            'Buffers':2,
            'Cached':3,
            'SReclaimable':4,
            'Shmem':5,
            'SwapTotal':6,
            'SwapFree':7}
        #self.val['MemUsed'] = self.val['MemTotal'] - self.val['MemFree'] - self.val['Buffers'] - self.val['Cached'] - self.val['SReclaimable'] + self.val['Shmem']

        mem_stat = [0 for _ in xrange(8)]
        for l in self.get_stat(stat_name, ':'):
            if l[0] in stats:
                mem_stat[stats[l[0]]] = long(l[1])/1024

        mem_used = mem_stat[0] - mem_stat[1] - mem_stat[2] - mem_stat[3] - mem_stat[4] + mem_stat[5]
        swap_used = mem_stat[6] - mem_stat[7]

        #used, free, buff, cache, swap used, swap free
        self.stat[stat_name] = (mem_used, mem_stat[1], mem_stat[2], mem_stat[3], swap_used, mem_stat[7])
        label = ('used', 'free', 'buffer', 'cache')
        return format_stat(label, self.stat[stat_name])

    def get_proc_stat(self, elapsed):
        stat_name = 'sys'
        stats = {'processes':0, 'procs_running':1, 'procs_blocked':2, 'intr':3, 'ctxt':4, 'softirq':5 }

        self.curr_stat[stat_name] = [0 for _ in xrange(6)]
        for l in self.get_stat(stat_name):
            if l[0] in stats:
                self.curr_stat[stat_name][stats[l[0]]] = long(l[1])

        val2 = self.curr_stat[stat_name]
        val1 = self.old_stat[stat_name]

        #proc_new, proc_running, proc_block, intrupts, ctx switchs, softirq
        self.stat[stat_name] = (1.0*(val2[0]-val1[0])/elapsed, val2[1], val2[2],
                           1.0*(val2[3]-val1[3])/elapsed, 1.0*(val2[4]-val1[4])/elapsed, 1.0*(val2[5]-val1[5])/elapsed)

        self.old_stat[stat_name] = val2
        label = ('new', 'running', 'block', 'intr', 'ctx', 'softirq')
        return format_stat(label, self.stat[stat_name])

    def get_tcp_conn_stat(self):
        conn_listen, conn_esta, conn_syn, conn_wait, conn_close = 0,0,0,0,0
        for l in self.get_tcp_stat():
            if l[3] in set(['0A']): conn_listen += 1
            elif l[3] in set(['01']): conn_esta += 1
            elif l[3] in set(['02', '03', '09']): conn_syn += 1
            elif l[3] in set(['06']): conn_wait += 1
            elif l[3] in set(['04', '05', '07', '08', '0B']): conn_close += 1

        self.stat['tcp_conns'] = (conn_listen,conn_esta, conn_syn, conn_wait, conn_close)
        label = ('listen', 'connected', 'syn', 'timewait', 'close')
        return format_stat(label, self.stat['tcp_conns'])

    def get_tcp_stat(self):
        for l in self.get_stat('tcp'):
            yield l
        for l in self.get_stat('tcp6'):
            yield l

    def get_load(self):
        stat_name = 'load'
        for l in self.get_stat(stat_name):
            if len(l) < 3: continue
            self.stat[stat_name] = (float(l[0]), float(l[1]), float(l[2]))

        label = ('load1', 'load5', 'load15')
        return format_stat(label, self.stat[stat_name])

    def get_uptime(self):
        stat_name = 'uptime'
        for l in self.get_stat(stat_name):
            if len(l) < 2: continue
            uptime = float(l[0])
            self.curr_stat[stat_name] = (uptime,)
            return uptime

    def get_net_nics(self):
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(self.host, 22, self.user, self.password)
        command = 'cat /proc/net/dev'
        std_in, std_out, std_err = ssh_client.exec_command(command)
        fd = std_out
        nic_filter = re.compile("^(lo|face|docker\d+)$")
        ret = []
        for line in fd.readlines():
            l = line.replace(':', ' ').split()
            if len(l) < 17 or nic_filter.match(l[0]):
                continue
            try:
                ret.append(l[0])
            except:
                pass
        return ret

    def get_mounted_dev(self):
        mounted_dev = set()
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(self.host, 22, self.user, self.password)
        command = 'cat /etc/mtab'
        std_in, std_out, std_err = ssh_client.exec_command(command)
        f = std_out
        for i in f.readlines():
            # /dev/xvda1 / ext4 rw,errors=remount-ro 0 0
            s = i.split()
            if len(s) >= 4 and s[2] not in IGNORE_FS:
                dev = s[0]
                if dev.startswith('/dev/mapper'):
                    try:
                        dev = os.path.basename(os.readlink(dev))
                    except:
                        dev = None
                else:
                    # /dev/xvda1 => xvda
                    dev = re.sub('\d+$', '', os.path.basename(dev))

                if dev:
                    mounted_dev.add(dev)
        return mounted_dev

    def get_block_devices(self):
        disk_filter = re.compile('^(loop|ram|sr|asm)\d+$')
        ret = []
        mounted_dev = self.get_mounted_dev()
        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(self.host, 22, self.user, self.password)
        command = 'ls -l /sys/block/*'
        std_in, std_out, std_err = ssh_client.exec_command(command)
        fd = std_out
        for l in std_out.readlines():
            dev_name = l.split('/')[-1]
            dev_name = re.sub(':','',dev_name).strip()
            if disk_filter.match(dev_name):
                continue
            if dev_name in mounted_dev:
                ret.append(dev_name)
        return ret[:30]


    def get_net_stat(self, elapsed):
        stat_name = 'net'
        net_nics = self.get_net_nics()
        ret = []
        label = ('recv', 'send')
        for l in self.get_stat(stat_name,':'):
            if l[0] in net_nics and len(l) >= 17:
                stat_nic = '%s_%s' % (stat_name, l[0])
                #net recv, net send, kb
                stat_curr = (long(l[1]), long(l[9]))
                stat_old = self.old_stat[stat_nic]
                self.stat[stat_nic] = tuple(1.0*(stat_curr[i] - stat_old[i])/elapsed/1024 for i in xrange(2))
                self.old_stat[stat_nic] = stat_curr

                netstat = format_stat(label, self.stat[stat_nic])
                netstat['nic'] = l[0]
                ret.append(netstat)
        return ret

    def get_io_stat(self, elapsed):
        self.block_devices = self.get_block_devices()
        stat_name = 'io'
        self.curr_stat['io'] = tuple(0 for _ in xrange(11))
        ret = []
        for l in self.get_stat(stat_name):
            if l[2] in self.block_devices and len(l) >= 14:
                #total io stat
                self.curr_stat['io'] = tuple(self.curr_stat['io'][i] + long(l[i+3]) for i in xrange(11))

                #per disk io stat
                self.curr_stat['io_' + l[2]] = tuple(long(l[i+3]) for i in xrange(11))

        #https://www.percona.com/doc/percona-toolkit/2.1/pt-diskstats.html
        for disk in ['io'] + ['io_' + d for d in self.block_devices]:
            if disk not in self.curr_stat or disk not in self.old_stat:
                continue
            rd, rd_mrg, rd_sec, rd_tim, wr, wr_mrg, wr_sec, wr_tim, in_prg, t1, t2 = tuple(
                1.0*(self.curr_stat[disk][i] - self.old_stat[disk][i]) for i in range(11))
            in_prg = self.curr_stat[disk][8]

            rd_rt, wr_rt, busy, io_s, qtime, ttime, stime = tuple(0 for i in xrange(7))

            if rd + rd_mrg > 0:
                rd_rt = rd_tim / (rd + rd_mrg)
            if wr + wr_mrg > 0:
                wr_rt = wr_tim / (wr + wr_mrg)
            busy = 100 * t1 / 1000 / elapsed
            io_s = (rd + wr ) / elapsed
            if rd + rd_mrg + wr + wr_mrg > 0:
                stime = t1 / (rd + rd_mrg + wr + wr_mrg)
            if rd + rd_mrg + wr + wr_mrg + in_prg > 0:
                ttime = t2 / (rd + rd_mrg + wr + wr_mrg + in_prg)

            qtime = ttime - stime

            rd_s, rd_avgkb, rd_m_s, rd_cnc, rd_mrg_s, wr_s, wr_avgkb, wr_m_s, wr_cnc, wr_mrg_s = tuple(0 for i in xrange(10))

            rd_s = rd / elapsed
            if rd > 0:
                rd_avgkb = rd_sec / rd / 2
            rd_m_s = rd_sec / 2 / 1024 / elapsed
            rd_cnc = rd_tim / 1000 / elapsed
            rd_mrg_s = rd_mrg / elapsed

            wr_s = wr / elapsed
            if wr > 0:
                wr_avgkb = wr_sec / wr / 2
            wr_m_s = wr_sec / 2 / 1024 / elapsed
            wr_cnc = wr_tim / 1000 / elapsed
            wr_mrg_s = wr_mrg / elapsed

            # io_read, io_write, io_queue, io_await, io_svctm, io_util, io_read_mb, io_write_mb,
            if disk == 'io':
                #total disk io stat
                self.stat[disk] = (rd_s, wr_s, in_prg, ttime, stime, busy, rd_m_s, wr_m_s)
            else:
                #per disk io stat
                # self.stat[disk] = (rd_s, rd_avgkb, rd_m_s, rd_mrg_s, rd_cnc, rd_rt,
                #               wr_s, wr_avgkb, wr_m_s, wr_mrg_s, wr_cnc, wr_rt,
                #               busy, in_prg, io_s, qtime, stime)
                label = ('reads', 'read_mb', 'read_rt', 'writes', 'write_mb', 'write_rt', 'ioutil', 'iops', 'qtime', 'stime')
                self.stat[disk] = (rd_s, rd_m_s, rd_rt,
                                   wr_s, wr_m_s, wr_rt,
                                   busy, io_s, qtime, stime)

                diskstat = format_stat(label, self.stat[disk])
                diskstat['disk'] = disk[3:]
                ret.append(diskstat)
            self.old_stat[disk] = self.curr_stat[disk]
        return ret

    def get_stat(self, stat_name, replace=None):
        stat_file = stat_file_config[stat_name]

        ssh_client = paramiko.SSHClient()
        ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        ssh_client.connect(self.host, 22, self.user, self.password)
        command = 'cat ' + stat_file
        std_in, std_out, std_err = ssh_client.exec_command(command)
        fd = std_out

        for l in fd.readlines():
            if replace is not None:
                yield l.replace(replace, ' ').split()
            else:
                yield l.split()


if __name__ == '__main__':
    linuxstat = LinuxStat('192.168.48.10', 'root', 'oracle')
    while True:
        stat = linuxstat.get_linux()
        print stat
