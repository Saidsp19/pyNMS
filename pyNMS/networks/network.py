# Copyright (C) 2017 Antoine Fourmy <antoine dot fourmy at gmail dot com>
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from .graph import Graph
from autonomous_system.AS import AS_class
from objects import objects
import random
import re
import warnings
from copy import copy
from ip_networks.configuration import RouterConfiguration
from objects.objects import *
from miscellaneous.network_functions import *
from math import cos, sin, asin, radians, sqrt, ceil, log
from collections import defaultdict, deque, OrderedDict
from heapq import heappop, heappush, nsmallest
from operator import getitem, itemgetter
from itertools import combinations
from miscellaneous.union_find import UnionFind
try:
    import numpy as np
    from cvxopt import matrix, glpk, solvers
except ImportError:
    warnings.warn('Package missing: linear programming functions will fail')

class Network(Graph):
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.nodes = {}
        self.plinks = {}
        self.l2links = {}
        self.l3links = {}
        self.traffics = {}
        self.interfaces = set()
        
        # pn for 'pool network'
        self.pn = {
                   'node': self.nodes, 
                   'plink': self.plinks, 
                   'l2link': self.l2links, 
                   'l3link': self.l3links,
                   'traffic': self.traffics, 
                   'interface': self.interfaces
                   }
        self.pnAS = {}
        # useful for tests and listbox when we want to retrieve an object
        # based on its name. The only object that needs changing when a object
        # is renamed by the user.
        self.name_to_id = {}
        
        # dicts used for IP networks 
        # - finds all layer-n segments networks, i.e all layer-n-capable 
        # interfaces that communicate via a layer-(n-1) device
        self.ma_segments = defaultdict(set)
        # string IP <-> IP mapping for I/E + parameters saving
        self.ip_to_oip = {}
        
        # osi layer to devices
        self.osi_layers = {
        3: ('router', 'host', 'cloud'),
        2: ('switch', 'optical switch'),
        1: ('regenerator', 'splitter', 'antenna')
        }
        
    # function filtering AS either per layer or per subtype
    def ASftr(self, filtering_mode, *sts):
        if filtering_mode == 'layer':
            keep = lambda r: r.layer in sts
        else:
            keep = lambda r: r.AS_type in sts
        return filter(keep, self.pnAS.values())
        
    # function that retrieves all IP addresses attached to a node, including
    # it's loopback IP.
    def attached_ips(self, src):
        for _, plink in self.graph[src.id]['plink']:
            yield plink('ip_address', src)
        yield src.ip_address
        
    # function that retrieves all next-hop IP addresses attached to a node, 
    # including the loopback addresses of its neighbors
    def nh_ips(self, src):
        for nh, plink in self.graph[src.id]['plink']:
            yield plink('ip_address', nh)
            yield nh.ip_address
        
    def OIPf(self, str_ip, interface=None):
        # creates  or retrieves an OIP based on a string IP ('IP/subnet' format)
        # the interface should always be specified at creation
        if str_ip in self.ip_to_oip:
            return self.ip_to_oip[str_ip]
        if interface:
            try:
                ip_addr, subnet = str_ip.split('/')
                OIP = IPAddress(ip_addr, int(subnet), interface)
            except ValueError:
                # wrong IP address format
                OIP = None
            self.ip_to_oip[str_ip] = OIP
            return OIP
        
    def AS_factory(
                   self, 
                   AS_type = 'RIP',
                   name = None, 
                   id = 0,
                   plinks = set(), 
                   nodes = set(),
                   imp = False
                   ):
        if not name:
            name = 'AS' + str(self.cpt_AS)
        if name not in self.pnAS:
            # creation of the AS
            self.pnAS[name] = AS_class[AS_type](
                                                self.view,
                                                name, 
                                                id,
                                                plinks, 
                                                nodes,
                                                imp
                                                )
            # increase the AS counter by one
            self.cpt_AS += 1
        return self.pnAS[name]
        
    ## Retrieve the credentials
    
    def get_credentials(self, node):
        credentials = self.view.controller.credentials_window.get_credentials()
        for property in ('username', 'password', 'enable_password', 'ip_address'):
            value = getattr(node, property)
            if value:
                credentials[property] = value
        return credentials
            
    ## Conversion methods and property -> type mapping
    
    # methods used to convert a string to an object 
    
    # convert an AS name to an AS
    def convert_AS(self, AS_name):
        return self.AS_factory(name=AS_name)
    
    # convert a string IP ('IP/subnet') to an 'Object IP'
    def convert_IP(self, ip):
        return self.OIPf(ip)
        
    def find_edge_nodes(self, AS):
        AS.pAS['edge'].clear()
        for node in AS.nodes:
            if any(
                   n not in AS.nodes 
                   for n, _ in self.graph[node.id]['plink']
                   ):
                AS.pAS['edge'].add(node)
                yield node
                
    # site management
    def add_to_site(self, site, *objects):
        for obj in objects:
            site.ps[obj.class_type].add(obj)
            
    def remove_from_site(self, site, *objects):
        for obj in objects:
            site.ps[obj.class_type].remove(obj)
  
    def update_AS_topology(self):
        for AS in self.ASftr('subtype', 'ISIS', 'OSPF', 'BGP'):
            # for all OSPF, IS-IS and BGP AS, fill the ABR/L1L2/nodes/links 
            # sets based on nodes area (ISIS, BGP) and vice-versa (OSPF)
            AS.update_AS_topology()
            
    def segment_finder(self, layer):
        # we associate a set of physical links to each layer-n segment.
        # at this point, there isn't any IP allocated yet: we cannot assign
        # IP addresses until we know the network layer-n segment topology.
        # we use that topology to create layer-n virtual connection
        # we keep the set of all physical links we've already visited 
        visited_plinks = set()
        # we loop through all the layer-n-networks boundaries
        for router in self.ftr('node', *self.osi_layers[layer]):
            # we start by looking at all attached physical links, and when we find one
            # that hasn't been visited yet, we don't stop until we've discovered
            # all network's physical links (i.e until we've reached all boundaries 
            # of that networks: routers or host).
            for neighbor, plink in self.graph[router.id]['plink']:
                if plink in visited_plinks:
                    continue
                visited_plinks.add(plink)
                # we update the set of physical linkss of the network 
                # as we discover them
                current_network = {(plink, router)}
                if any(neighbor.subtype in self.osi_layers[l] for l in range(1, layer)):
                    # we add the neighbor of the router in the stack: we'll fill 
                    # the stack with nodes as we discover them, provided that 
                    # these nodes are not boundaries, i.e not router or host
                    stack_network = [neighbor]
                    visited_nodes = {router}
                    while stack_network:
                        curr_node = stack_network.pop()
                        for node, adj_plink in self.graph[curr_node.id]['plink']:
                            if node in visited_nodes:
                                continue
                            visited_plinks.add(adj_plink)
                            visited_nodes.add(node)
                            if any(node.subtype in self.osi_layers[l] 
                                                    for l in range(1, layer)):
                                stack_network.append(node)
                            else:
                                current_network.add((adj_plink, node))
                else:
                    current_network.add((plink, neighbor))
                self.ma_segments[layer].add(frozenset(current_network))
        
    def multi_access_network(self, layer):
        # we create the virtual connnections at layer 2 and 3, that is the 
        # links between adjacent Ln devices (L2-L2, L3-L3).
        link_type = 'l{layer}link'.format(layer = layer)
        vc_type = 'l{layer}vc'.format(layer = layer)
        
        for ma_network in self.ma_segments[layer]:
            for source_plink, node in ma_network:
                allowed_neighbors = ma_network - {(source_plink, node)}
                for destination_plink, neighbor in allowed_neighbors:
                    if not self.is_connected(node, neighbor, link_type, vc_type):
                        vc = self.lf(
                                     source = node, 
                                     destination = neighbor, 
                                     subtype = vc_type
                                     )
                        vc('link', node, source_plink)
                        vc('link', neighbor, destination_plink)
                        
    def vc_creation(self):
        # clear all existing multi-access segments
        self.ma_segments.clear()
        for i in (2, 3):
            type, subtype = 'l{}link'.format(i), 'l{}vc'.format(i)
            self.segment_finder(i)
            self.multi_access_network(i)
            
    def clear_ip(self):
        # remove all existing IP addresses
        self.ip_to_oip.clear()
        # reset all traffic links source and destination IP as new IP will
        # be assigned
        for traffic in self.traffics.values():
            traffic.source_IP = traffic.destination_IP = None
            
    def ip_allocation(self):
        self.clear_ip()
        # we will perform the IP addressing of all subnetworks with VLSM
        # we first sort all subnetworks in increasing order of size, then
        # compute which subnet is needed
        subnetworks = sorted(list(self.ma_segments[3]), key=len)
        subnetwork_ip = '10.0.0.0'
        while subnetworks:
            # we retrieve the biggest subnetwork not yet treated
            subnetwork = subnetworks.pop()
            # both network and broadcast addresses are excluded:
            # we add 2 to the size of the subnetwork
            size = ceil(log(len(subnetwork) + 2, 2))
            subnet = 32 - size
            for idx, (plink, node) in enumerate(subnetwork, 1):
                curr_ip = ip_incrementer(subnetwork_ip, idx)
                ip_addr = IPAddress(curr_ip, subnet, plink('interface', node))
                self.ip_to_oip[str(ip_addr)] = ip_addr
                plink('ip_address', node, ip_addr)
                plink.subnetwork = ip_addr.network
            subnetwork_ip = ip_incrementer(subnetwork_ip, 2**size)
            
        # allocate loopback address using the 192.168.0.0/16 private 
        # address space
        for idx, router in enumerate(self.ftr('node', 'router'), 1):
            router.ip_address = '192.168.{}.{}'.format(idx // 255, idx % 255)
            
    def mac_allocation(self):
        # ranges of private MAC addresses
        # x2:xx:xx:xx:xx:xx
        # x6:xx:xx:xx:xx:xx
        # xA:xx:xx:xx:xx:xx
        # xE:xx:xx:xx:xx:xx
        
        # allocation of mac_x2 and mac_x6 for interfaces MAC address
        mac_x2, mac_x6 = '020000000000', '060000000000'
        for id, plink in enumerate(self.plinks.values(), 1):
            macS, macD = mac_incrementer(mac_x2, id), mac_incrementer(mac_x6, id)
            source_mac = ':'.join(macS[i:i+2] for i in range(0, 12, 2))
            destination_mac = ':'.join(macD[i:i+2] for i in range(0, 12, 2))
            plink.interfaceS.mac_address = source_mac
            plink.interfaceD.mac_address = destination_mac
            
        # allocation of mac_xA for switches base (hardware) MAC address
        mac_xA = '0A0000000000'
        for id, switch in enumerate(self.ftr('node', 'switch', 1)):
            switch.base_mac_address = mac_incrementer(mac_xA, id)

    def interface_allocation(self):
        for node in self.nodes.values():
            for idx, (_, adj_plink) in enumerate(self.graph[node.id]['plink']):
                adj_plink('name', node, 'FastEthernet 0/{}'.format(idx))
                
    def interface_configuration(self):
        self.mac_allocation()
        self.ip_allocation()
        self.interface_allocation()
        
    def mininet_configuration(self):
        first_letter = {'host': 'h', 'sdn_switch': 's', 'sdn_controller': 'c'}
        for subtype in ('host', 'sdn_switch', 'sdn_controller'):
            letter = first_letter[subtype]
            for idx, node in enumerate(self.ftr('node', subtype), 1):
                node.mininet_name = letter + str(idx)
                
    # WC physical link dimensioning: this computes the maximum traffic the physical link
    # may have to carry considering all possible physical link failure. 
    # NetDim fails all physical links of the network one by one, and evaluates 
    # the impact in terms of bandwidth for each physical link. 
    # The highest value is kept in memory, as well as the physical link which failure 
    # induces this value.
    def plink_dimensioning(self):
        # we need to remove all failures before dimensioning the physical links:
        # the set of failed physical link will be redefined, but we also need the
        # icons to be cleaned from the canvas
        self.view.remove_failures()
        
        # we consider each physical link in the network to be failed, one by one
        for failed_plink in self.plinks.values():
            self.failed_obj = {failed_plink}
            # the physical link being failed, we will recreate all routing tables
            # then use the path finding procedure to map the traffic flows
            self.routing_table_creation()
            self.path_finder()
            for plink in self.plinks.values():
                for dir in ('SD', 'DS'):
                    curr_traffic = getattr(plink, 'traffic' + dir)
                    if curr_traffic > getattr(plink, 'wctraffic' + dir):
                        setattr(plink, 'wctraffic' + dir, curr_traffic)
                        setattr(plink, 'wcfailure', str(failed_plink))
        self.failed_obj.clear()
                    
    # this function creates both the ARP and the RARP tables
    def arpt_creation(self):
        # clear the existing ARP tables
        for router in self.ftr('node', 'router'):
            router.arpt.clear()
        for l3_segments in self.ma_segments[3]:
            for (plinkA, routerA) in l3_segments:
                for (plinkB, routerB) in l3_segments: 
                    remote_ip = plinkB('ip_address', routerB)
                    remote_mac = plinkB('mac_address', routerB)
                    outgoing_if = plinkA('name', routerA)
                    routerA.arpt[remote_ip] = (remote_mac, outgoing_if)
            
    def STP_update(self):
        for AS in self.ASftr('subtype', 'STP'):
            AS.root_election()
            AS.build_SPT()
            
    def st_creation(self):
        # clear the existing switching table
        for switch in self.ftr('node', 'switch'):
            switch.st.clear()
        for AS in self.ASftr('subtype', 'STP'):
            for switch in AS.nodes:
                self.ST_builder(switch, AS.pAS['link'] - AS.SPT_links)
        # if the switch isn't part of an STP AS, we build its switching table
        # without excluding any physical link
        for switch in self.ftr('node', 'switch'):
            if not switch.st:
                self.ST_builder(switch)
                
    def reset_traffic(self):
        # reset the traffic for all physical links
        for plink in self.plinks.values():
            plink.trafficSD = plink.trafficDS = 0.
                
    def path_finder(self):
        self.reset_traffic()
        for traffic in self.traffics.values():
            src, dest = traffic.source, traffic.destination
            if all(node.subtype == 'router' for node in (src, dest)):
                self.RFT_path_finder(traffic)
            else:
                _, traffic.path = self.A_star(src, dest)
            if not traffic.path:
                print('no path found for {}'.format(traffic))
                
    ## A) Ethernet switching table
    
    def ST_builder(self, source, excluded_plinks=None):
        
        if not excluded_plinks:
            excluded_plinks = set()
        
        visited = set()
        heap = [(source, [], [], None)]
        
        while heap:
            node, path_node, path_plink, ex_int = heappop(heap)  
            if node not in visited:
                visited.add(node)
                for neighbor, l2vc in self.gftr(node, 'l2link', 'l2vc'):
                    adj_plink = l2vc('link', node)
                    remote_plink = l2vc('link', neighbor)
                    if adj_plink in path_plink:
                        continue
                    if adj_plink in excluded_plinks: 
                        continue
                    if node == source:
                        ex_int = adj_plink('interface', source)
                        mac = remote_plink('mac_address', neighbor)
                        source.st[mac] = ex_int
                    heappush(heap, (neighbor, path_node + [neighbor], 
                                            path_plink + [adj_plink], ex_int))
                    
            if path_plink:
                plink, ex_tk = path_plink[-1], path_plink[0]
                source.st[plink.interfaceS.mac_address] = ex_tk('interface', source)
                source.st[plink.interfaceD.mac_address] = ex_tk('interface', source)
    
    ## 1) RFT-based routing and dimensioning
    
    def RFT_path_finder(self, traffic):
        source, destination = traffic.source, traffic.destination
        src_ip, dst_ip = traffic.source_IP, traffic.destination_IP
        valid = bool(src_ip) & bool(dst_ip)
        
        if valid:
            dst_ntw = dst_ip.network
        # (current node, physical link from which the data flow comes, dataflow)
        heap = [(source, None, None)]
        path = set()
        path_str = []
        while heap and valid:
            curr_node, curr_plink, dataflow = heap.pop()
            path.add(curr_node)
            # data flow creation
            if not dataflow:
                dataflow = DataFlow(src_ip, dst_ip)
                dataflow.throughput = traffic.throughput
            if curr_node == destination:
                continue
            if curr_node.subtype == 'router':
                if dst_ntw in curr_node.rt:
                    routes = curr_node.rt[dst_ntw]
                # if we cannot find the destination address in the routing table, 
                # and there is a default route, we use it.
                elif '0.0.0.0' in curr_node.rt:
                    routes = curr_node.rt['0.0.0.0']
                else:
                    warnings.warn('Path not found for {}'.format(traffic))
                    break
                # we count the number of physical links in failure
                failed_plinks = sum(r[-1] in self.failed_obj for r in routes)
                # and remove them from share so that they are ignored for 
                # physical link dimensioning
                for idx, route in enumerate(routes):
                    _, nh_ip, ex_int, _, router, ex_tk = route
                    # we create a new dataflow based on the old one
                    new_dataflow = copy(dataflow)
                    # the throughput depends on the number of ECMP routes
                    new_dataflow.throughput /= len(routes) - failed_plinks
                    # the source MAC address is the MAC address of the interface
                    # used to exit the current node
                    new_dataflow.src_mac = ex_int.mac_address
                    # the destination MAC address is the MAC address
                    # corresponding to the next-hop IP address in the ARP table
                    # we take the first element as the ARP table is built as 
                    # a mapping IP <-> (MAC, outgoing interface)
                    new_dataflow.dst_mac = curr_node.arpt[nh_ip][0]
                    sd = (curr_node == ex_tk.source)*'SD' or 'DS'
                    ex_tk.__dict__['traffic' + sd] += new_dataflow.throughput
                    # add the exit physical link to the path
                    path.add(ex_tk)
                    # the next-hop is the node at the end of the exit physical link
                    next_hop = ex_tk.source if sd == 'DS' else ex_tk.destination
                    heap.append((next_hop, ex_tk, new_dataflow))
                    if not idx:
                        path_str.append('''
                Current_node: {curr_node}
                Next-hop: {next_hop}
                Next-hop IP address: {nh_ip}
                Destination MAC address: {dst_mac}
                Outgoing physical link: {ex_tk}
                Outgoing interface: {ex_int}'''.format(
                                                curr_node = curr_node,
                                                next_hop = next_hop,
                                                nh_ip = nh_ip,
                                                dst_mac = new_dataflow.dst_mac,
                                                ex_tk = ex_tk,
                                                ex_int = ex_int
                                                ))
                    
            if curr_node.subtype == 'switch':
                # we find the exit interface based on the destination MAC
                # address in the switching table, the dataflow itself remains
                # unaltered
                ex_int = curr_node.st[dataflow.dst_mac]
                ex_tk = ex_int.link
                path.add(ex_tk)
                # we append the next hop to the heap
                if ex_tk.source == curr_node:
                    next_hop = ex_tk.destination
                else:
                    next_hop = ex_tk.source
                heap.append((next_hop, ex_tk, dataflow))
                path_str.append('''
                Current_node: {curr_node}
                Next-hop: {next_hop}
                Outgoing physical link: {ex_tk}
                Outgoing interface: {ex_int}'''.format(
                                                curr_node = curr_node,
                                                next_hop = next_hop,
                                                ex_tk = ex_tk,
                                                ex_int = ex_int
                                                ))
        print(path)
        traffic.path = path
        return path, path_str
        
    ## 2) Add connected interfaces to the RFT
    
    def static_RFT_builder(self, source):
        
        for _, sr in self.gftr(source, 'l3link', 'static route', False):
            source.rt[sr.dst_sntw] = {('S', sr.nh_ip, None, 0, nh_node, None)}
                                                                
                    
        for neighbor, adj_l3vc in self.gftr(source, 'l3link', 'l3vc'):
            # if adj_plink in self.failed_obj:
            #     continue
            ex_ip = adj_l3vc('ip_address', neighbor)
            ex_int = adj_l3vc('interface', source)
            adj_plink = adj_l3vc('link', source)
            # we compute the subnetwork of the attached
            # interface: it is a directly connected interface
            source.rt[adj_plink.subnetwork] = {('C', ex_ip, ex_int, 
                                                    0, neighbor, adj_plink)}
                             
    def switching_table_creation(self):
        self.arpt_creation()
        self.STP_update()
        self.st_creation()
        
    def subnetwork_update(self):
        for ip in self.ip_to_oip.values():
            ip.interface.link.subnetwork = ip.network
        
    def routing_table_creation(self):
        self.subnetwork_update()
        # clear the existing routing tables
        for node in self.ftr('node', 'router', 'host'):
            node.rt.clear()
        # we compute the routing table of all routers
        for AS in self.ASftr('subtype', 'RIP', 'ISIS', 'OSPF'):
            AS.build_RFT()
        for router in self.ftr('node', 'router', 'host'):
            self.static_RFT_builder(router)
            
    def route(self):
        self.routing_table_creation()
        self.path_finder()
      
    ## Shortest path(s) algorithms
    
    ## 1) Dijkstra algorithm
        
    def dijkstra(
                 self, 
                 source, 
                 target,
                 allowed_plinks = None, 
                 allowed_nodes = None
                 ):
        
        if allowed_plinks is None:
            allowed_plinks = set(self.plinks.values())
        if allowed_nodes is None:
            allowed_nodes = set(self.nodes.values())
        
        prec_node = {i: None for i in allowed_nodes}
        prec_plink = {i: None for i in allowed_nodes}
        visited = set()
        dist = {i: float('inf') for i in allowed_nodes}
        dist[source] = 0
        heap = [(0, source)]
        while heap:
            dist_node, node = heappop(heap) 
            if node not in visited:
                visited.add(node)
                for neighbor, adj_plink in self.graph[node.id]['plink']:
                    # we ignore what's not allowed (not in the AS or in failure)
                    if neighbor not in allowed_nodes:
                        continue
                    if adj_plink not in allowed_plinks:
                        continue
                    dist_neighbor = dist_node + adj_plink('cost', node)
                    if dist_neighbor < dist[neighbor]:
                        dist[neighbor] = dist_neighbor
                        prec_node[neighbor] = node
                        prec_plink[neighbor] = adj_plink
                        heappush(heap, (dist_neighbor, neighbor))
                        
        # traceback the path from target to source
        curr, path_plink = target, [prec_plink[target]]
        while curr != source:
            curr = prec_node[curr]
            path_plink.append(prec_plink[curr])
                        
        # we return:
        # - the dist dictionnary, that contains the distance from the source
        # to any other node in the tree 
        # - the shortest path from source to target
        # - all edges that belong to the Shortest Path Tree
        # we need all three variables for Suurbale algorithm below
        return dist, path_plink[:-1][::-1], filter(None, prec_plink.values())
        
    ## 2) A* algorithm for CSPF modelization
            
    def A_star(
               self, 
               source, 
               target, 
               excluded_plinks = None, 
               excluded_nodes = None, 
               path_constraints = None, 
               allowed_plinks = None, 
               allowed_nodes = None
               ):
                
        # initialize parameters
        if excluded_nodes is None:
            excluded_nodes = set()
        if excluded_plinks is None:
            excluded_plinks = set()
        if path_constraints is None:
            path_constraints = []
        if allowed_plinks is None:
            allowed_plinks = set(self.plinks.values())
        if allowed_nodes is None:
            allowed_nodes = set(self.nodes.values())
            
        pc = [target] + path_constraints[::-1]
        visited = set()
        heap = [(0, source, [source], [], pc)]
        while heap:
            dist, node, nodes, plinks, pc = heappop(heap)
            if node not in visited:
                visited.add(node)
                if node == pc[-1]:
                    visited.clear()
                    heap.clear()
                    pc.pop()
                    if not pc:
                        return nodes, plinks
                for neighbor, adj_plink in self.graph[node.id]['plink']:
                    # excluded and allowed nodes
                    if neighbor not in allowed_nodes - excluded_nodes: 
                        continue
                    # excluded and allowed physical links
                    if adj_plink not in allowed_plinks - excluded_plinks: 
                        continue
                    heappush(heap, (
                                    dist + adj_plink('cost', node), 
                                    neighbor,
                                    nodes + [neighbor], 
                                    plinks + [adj_plink], 
                                    pc
                                    )
                            )
        return [], []

    ## 3) Bellman-Ford algorithm
        
    def bellman_ford(
                     self, 
                     source, 
                     target, 
                     cycle = False,
                     excluded_plinks = None, 
                     excluded_nodes = None, 
                     allowed_plinks = None, 
                     allowed_nodes = None
                     ):
        
        # initialize parameters
        if excluded_nodes is None:
            excluded_nodes = set()
        if excluded_plinks is None:
            excluded_plinks = set()
        if allowed_plinks is None:
            allowed_plinks = set(self.plinks.values())
        if allowed_nodes is None:
            allowed_nodes = set(self.nodes.values())

        n = len(allowed_nodes)
        prec_node = {i: None for i in allowed_nodes}
        prec_plink = {i: None for i in allowed_nodes}
        dist = {i: float('inf') for i in allowed_nodes}
        dist[source] = 0
        
        for i in range(n+2):
            negative_cycle = False
            for node in allowed_nodes:
                for neighbor, adj_plink in self.graph[node.id]['plink']:
                    sd = (node == adj_plink.source)*'SD' or 'DS'
                    # excluded and allowed nodes
                    if neighbor not in allowed_nodes - excluded_nodes: 
                        continue
                    # excluded and allowed physical links
                    if adj_plink not in allowed_plinks - excluded_plinks: 
                        continue
                    dist_neighbor = dist[node] + getattr(adj_plink, 'cost' + sd)
                    if dist_neighbor < dist[neighbor]:
                        dist[neighbor] = dist_neighbor
                        prec_node[neighbor] = node
                        prec_plink[neighbor] = adj_plink
                        negative_cycle = True
                        
        # traceback the path from target to source
        if dist[target] != float('inf') and not cycle:
            curr, path_node, path_plink = target, [target], [prec_plink[target]]
            while curr != source:
                curr = prec_node[curr]
                path_plink.append(prec_plink[curr])
                path_node.append(curr)
            return path_node[::-1], path_plink[:-1][::-1]
        # if we want a cycle, and one exists, we find it
        if cycle and negative_cycle:
                curr, path_node, path_plink = target, [target], [prec_plink[target]]
                # return the cycle itself (for the cycle cancelling algorithm) 
                # starting from the target, we go through the predecessors 
                # we find any cycle (we don't necessarily have to come back to
                # the target).
                while curr not in path_node:
                    curr = prec_node[curr]
                    path_plink.append(prec_plink[curr])
                    path_node.append(curr)
                return path_node[::-1], path_plink[:-1][::-1]
        # if we didn't find a path, and were not looking for a cycle, 
        # we return empty lists
        return [], []
            
    ## 4) Floyd-Warshall algorithm
            
    def floyd_warshall(self):
        nodes = list(self.nodes.values())
        n = len(nodes)
        W = [[0]*n for _ in range(n)]
        
        for id1, n1 in enumerate(nodes):
            for id2, n2 in enumerate(nodes):
                if id1 != id2:
                    for neighbor, plink in self.graph[n1.id]['plink']:
                        if neighbor == n2:
                            W[id1][id2] = plink.costSD
                            break
                    else:
                        W[id1][id2] = float('inf')
                        
        for k in range(n):
            for u in range(n):
                for v in range(n):
                    W[u][v] = min(W[u][v], W[u][k] + W[k][v])
                    
        if any(W[v][v] < 0 for v in range(n)):
            return False
        else:
            all_length = defaultdict(dict)
            for id1, n1 in enumerate(nodes):
                for id2, n2 in enumerate(nodes):
                    all_length[n1][n2] = W[id1][id2]
                    
        return all_length  
        
    ## 5) DFS (all loop-free paths)
        
    def all_paths(self, source, target=None):
        # generates all loop-free paths from source to optional target
        path = [source]
        seen = {source}
        def find_all_paths():
            dead_end = True
            node = path[-1]
            if node == target:
                yield list(path)
            else:
                for neighbor, adj_plink in self.graph[node.id]['plink']:
                    if neighbor not in seen:
                        dead_end = False
                        seen.add(neighbor)
                        path.append(neighbor)
                        yield from find_all_paths()
                        path.pop()
                        seen.remove(neighbor)
            if not target and dead_end:
                yield list(path)
        yield from find_all_paths()
        
    ## Link-disjoint / link-and-node-disjoint shortest pair algorithms
    
    ## 1) A* link-disjoint pair search
    
    def A_star_shortest_pair(self, source, target, a_n=None, a_t=None):
        # To find the shortest pair from the source to the target, we look
        # for the shortest path going from the source to the source, with 
        # the target as a 'path constraint'.
        # Each path is stored with sets of allowed nodes and physical links that will 
        # contains what belongs to the first path, once we've reached the target.
        
        # if a_n is None:
        #     a_n = AS.nodes
        # if a_t is None:
        #     a_t = AS.pAS['link']
        
        if a_t is None:
            a_t = set(self.plinks.values())
        if a_n is None:
            a_n = set(self.nodes.values())

        visited = set()
        # in the heap, we store e_o, the list of excluded objects, which is
        # empty until we reach the target.
        heap = [(0, source, [], set())]
        while heap:
            dist, node, path_plink, e_o = heappop(heap)  
            if (node, tuple(path_plink)) not in visited:
                visited.add((node, tuple(path_plink)))
                if node == target:
                    e_o = set(path_plink)
                if node == source and e_o:
                    return [], path_plink
                for neighbor, adj_plink in self.graph[node.id]['plink']:
                    sd = (node == adj_plink.source)*'SD' or 'DS'
                    # we ignore what's not allowed (not in the AS or in failure
                    # or in the path we've used to reach the target)
                    if neighbor not in a_n or adj_plink not in a_t-e_o:
                        continue
                    cost = getattr(adj_plink, 'cost' + sd)
                    heappush(heap, (dist + cost, neighbor, 
                                                path_plink + [adj_plink], e_o))
        return [], []
        
    ## 2) Bhandari algorithm for link-disjoint shortest pair
        
    def bhandari(self, source, target, a_n=None, a_t=None):
    # - we find the shortest path from source to target using A* algorithm
    # - we replace bidirectionnal physical links of the shortest path with 
    # unidirectional physical links with a negative cost
    # - we run Bellman-Ford algorithm to find the new 
    # shortest path from source to target
    # - we remove all overlapping physical links
        
        if a_t is None:
            a_t = set(self.plinks.values())
        if a_n is None:
            a_n = set(self.nodes.values())
            
        # we store the cost value in the flow parameters, since bhandari 
        # algorithm relies on graph transformation, and the costs of the edges
        # will be modified.
        # at the end, we will revert the cost to their original value
        for plink in a_t:
            plink.flowSD = plink.costSD
            plink.flowDS = plink.costDS
            
        _, first_path = self.A_star(
                              source, 
                              target, 
                              allowed_plinks = a_t, 
                              allowed_nodes = a_n
                              ) 
                   
        # we set the cost of the shortest path physical linkss to float('inf'), 
        # which is equivalent to just removing them. In the reverse direction, 
        # we set the cost to -1.
        current_node = source
        for plink in first_path:
            dir = 'SD' * (current_node == plink.source) or 'DS'
            reverse_dir = 'SD' if dir == 'DS' else 'DS'
            setattr(plink, 'cost' + dir, float('inf'))
            setattr(plink, 'cost' + reverse_dir, -1)
            current_node = plink.destination if dir == 'SD' else plink.source
            
        _, second_path = self.bellman_ford(
                                           source, 
                                           target, 
                                           allowed_plinks = a_t, 
                                           allowed_nodes = a_n
                                           )
        
        for plink in a_t:
            plink.costSD = plink.flowSD
            plink.costDS = plink.flowDS

        return set(first_path) ^ set(second_path)
        
    def suurbale(self, source, target, a_n=None, a_t=None):
    # - we find the shortest path tree from the source using dijkstra algorithm
    # - we change the cost of all edges (a,b) such that
    # c(a, b) = c(a, b) - d(s, b) + d(s, a) (all tree edge will have a 
    # resulting cost of 0 with that formula, since c(a, b) = d(s, a) - d(s, b)
    # - we run A* algorithm to find the new 
    # shortest path from source to target
    # - we remove all overlapping physical links
        
        if a_t is None:
            a_t = set(self.plinks.values())
        if a_n is None:
            a_n = set(self.nodes.values())
            
        # we store the cost value in the flow parameters, since bhandari 
        # algorithm relies on graph transformation, and the costs of the edges
        # will be modified.
        # at the end, we will revert the cost to their original value
        for plink in a_t:
            plink.flowSD = plink.costSD
            plink.flowDS = plink.costDS
            
        dist, first_path, tree = self.dijkstra(
                              source, 
                              target, 
                              allowed_plinks = a_t, 
                              allowed_nodes = a_n
                              ) 
                              
        # we change the physical links' cost with the formula described above
        for plink in tree:
            # new_c(a, b) = c(a, b) - D(b) + D(a) where D(x) is the 
            # distance from the source to x.
            src, dest = plink.source, plink.destination
            plink.costSD += dist[src] - dist[dest]
            plink.costDS += dist[dest] - dist[src]
            
        # we exclude the edge of the shortest path (infinite cost)
        current_node = source
        for plink in first_path:
            dir = 'SD' * (current_node == plink.source) or 'DS'
            setattr(plink, 'cost' + dir, float('inf'))
            current_node = plink.destination if dir == 'SD' else plink.source
            
        _, second_path = self.A_star(
                              source, 
                              target, 
                              allowed_plinks = a_t, 
                              allowed_nodes = a_n
                              )
                              
        return set(first_path) ^ set(second_path)

        
    ## Flow algorithms
    
    def reset_flow(self):
        for plink in self.plinks.values():
            plink.flowSD = plink.flowDS = 0
    
    ## 1) Ford-Fulkerson algorithm
        
    def augment_ff(self, val, curr_node, target, visit):
        visit[curr_node] = True
        if curr_node == target:
            return val
        for neighbor, adj_plink in self.graph[curr_node.id]['plink']:
            direction = curr_node == adj_plink.source
            sd, ds = direction*'SD' or 'DS', direction*'DS' or 'SD'
            cap = getattr(adj_plink, 'capacity' + sd)
            current_flow = getattr(adj_plink, 'flow' + sd)
            if cap > current_flow and not visit[neighbor]:
                residual_capacity = min(val, cap - current_flow)
                global_flow = self.augment_ff(
                                              residual_capacity, 
                                              neighbor, 
                                              target, 
                                              visit
                                              )
                if global_flow > 0:
                    adj_plink.__dict__['flow' + sd] += global_flow
                    adj_plink.__dict__['flow' + ds] -= global_flow
                    return global_flow
        return False
        
    def ford_fulkerson(self, s, d):
        self.reset_flow()
        while self.augment_ff(float('inf'), s, d, {n:0 for n in self.pn['node'].values()}):
            pass
        # flow leaving from the source 
        return sum(
                  getattr(adj, 'flow' + (s==adj.source)*'SD' or 'DS') 
                  for _, adj in self.graph[s.id]['plink']
                  )
        
    ## 2) Edmonds-Karp algorithm
        
    def augment_ek(self, source, destination):
        res_cap = {n:0 for n in self.pn['node'].values()}
        augmenting_path = {n: None for n in self.pn['node'].values()}
        Q = deque()
        Q.append(source)
        augmenting_path[source] = source
        res_cap[source] = float('inf')
        while Q:
            curr_node = Q.popleft()
            for neighbor, adj_plink in self.graph[curr_node.id]['plink']:
                direction = curr_node == adj_plink.source
                sd, ds = direction*'SD' or 'DS', direction*'DS' or 'SD'
                cap = getattr(adj_plink, 'capacity' + sd)
                flow = getattr(adj_plink, 'flow' + sd)
                residual = cap - flow
                if residual and augmenting_path[neighbor] is None:
                    augmenting_path[neighbor] = curr_node
                    res_cap[neighbor] = min(res_cap[curr_node], residual)
                    if neighbor == destination:
                        break
                    else:
                        Q.append(neighbor)
        return augmenting_path, res_cap[destination]
        
    def edmonds_karp(self, source, destination):
        self.reset_flow()
        while True:
            augmenting_path, global_flow = self.augment_ek(source, destination)
            if not global_flow:
                break
            curr_node = destination
            while curr_node != source:
                # find the physical link between the two nodes
                prec_node = augmenting_path[curr_node]
                find_plink = lambda p: getitem(p, 0) == prec_node
                (_, plink) ,= filter(find_plink, self.graph[curr_node.id]['plink'])
                # define sd and ds depending on how the physical link is defined
                direction = curr_node == plink.source
                sd, ds = direction*'SD' or 'DS', direction*'DS' or 'SD'
                plink.__dict__['flow' + ds] += global_flow
                plink.__dict__['flow' + sd] -= global_flow
                curr_node = prec_node 
        return sum(
                   getattr(adj, 'flow' + ((source==adj.source)*'SD' or 'DS')) 
                   for _, adj in self.graph[source.id]['plink']
                  )
                  
    ## 2) Dinic algorithm
    
    def augment_di(self, level, flow, curr_node, dest, limit):
        if limit <= 0:
            return 0
        if curr_node == dest:
            return limit
        val = 0
        for neighbor, adj_plink in self.graph[curr_node.id]['plink']:
            direction = curr_node == adj_plink.source
            sd, ds = direction*'SD' or 'DS', direction*'DS' or 'SD'
            cap = getattr(adj_plink, 'capacity' + sd)
            flow = getattr(adj_plink, 'flow' + sd)
            residual = cap - flow
            if level[neighbor] == level[curr_node] + 1 and residual > 0:
                z = min(limit, residual)
                aug = self.augment_di(level, flow, neighbor, dest, z)
                adj_plink.__dict__['flow' + sd] += aug
                adj_plink.__dict__['flow' + ds] -= aug
                val += aug
                limit -= aug
        if not val:
            level[curr_node] = None
        return val
        
    def dinic(self, source, destination):
        self.reset_flow()
        Q = deque()
        total = 0
        while True:
            Q.appendleft(source)
            level = {node: None for node in self.nodes.values()}
            level[source] = 0
            while Q:
                curr_node = Q.pop()
                for neighbor, adj_plink in self.graph[curr_node.id]['plink']:
                    direction = curr_node == adj_plink.source
                    sd = direction*'SD' or 'DS'
                    cap = getattr(adj_plink, 'capacity' + sd)
                    flow = getattr(adj_plink, 'flow' + sd)
                    if level[neighbor] is None and cap > flow:
                        level[neighbor] = level[curr_node] + 1
                        Q.appendleft(neighbor)
                        
            if level[destination] is None:
                return flow, total
            limit = sum(
                        getattr(adj_plink, 'capacity' + 
                        ((source == adj_plink.source)*'SD' or 'DS'))
                        for _, adj_plink in self.graph[source.id]['plink']
                        )
            total += self.augment_di(level, flow, source, destination, limit)
        
    ## Minimum spanning tree algorithms 
    
    ## 1) Kruskal algorithm
        
    def kruskal(self, allowed_nodes):
        uf = UnionFind(allowed_nodes)
        edges = []
        for node in allowed_nodes:
            for neighbor, adj_plink in self.graph[node.id]['plink']:
                if neighbor in allowed_nodes:
                    edges.append((adj_plink.costSD, adj_plink, node, neighbor))
        for w, t, u, v in sorted(edges, key=itemgetter(0)):
            if uf.union(u, v):
                yield t
                
    ## Linear programming algorithms
    
    ## 1) Shortest path
    
    def LP_SP_formulation(self, s, t):

        # Solves the MILP: minimize c'*x
        #         subject to G*x + s = h
        #                     A*x = b
        #                     s >= 0
        #                     xi integer, forall i in I

        
        self.reset_flow()
        
        new_graph = {node: {} for node in self.nodes.values()}
        for node in self.nodes.values():
            for neighbor, plink in self.graph[node.id]['plink']:
                sd = (node == plink.source)*'SD' or 'DS'
                new_graph[node][neighbor] = getattr(plink, 'cost' + sd)

        n = 2*len(self.plinks)
        
        c = []
        for node in new_graph:
            for neighbor, cost in new_graph[node].items():
                # the float conversion is ESSENTIAL !
                # I first forgot it, then spent hours trying to understand 
                # what was wrong. If 'c' is not made of float, no explicit 
                # error is raised, but the result is sort of random !
                c.append(float(cost))
                
        # for the condition 0 < x_ij < 1
        h = np.concatenate([np.ones(n), np.zeros(n)])
        id = np.eye(n, n)
        G = np.concatenate((id, -1*id), axis=0).tolist()  
        
        # flow conservation: Ax = b
        A, b = [], []
        for node_r in new_graph:
            if node_r != t:
                b.append(float(node_r == s))
                row = []
                for node in new_graph:
                    for neighbor in new_graph[node]:
                        row.append(
                                   -1. if neighbor == node_r 
                              else  1. if node == node_r 
                              else  0.
                                   )
                A.append(row)
        
        A, G, b, c, h = map(matrix, (A, G, b, c, h))
        solsta, x = glpk.ilp(c, G.T, h, A.T, b)
        
        # update the resulting flow for each node
        cpt = 0
        for node in new_graph:
            for neighbor in new_graph[node]:
                new_graph[node][neighbor] = x[cpt]
                cpt += 1
                
        # update the network physical links with the new flow value
        for plink in self.plinks.values():
            src, dest = plink.source, plink.destination
            plink.flowSD = new_graph[src][dest]
            plink.flowDS = new_graph[dest][src]

        # traceback the shortest path with the flow
        curr_node, path_plink = s, []
        while curr_node != t:
            for neighbor, adj_plink in self.graph[curr_node.id]['plink']:
                # if the flow leaving the current node is 1, we move
                # forward and replace the current node with its neighbor
                if adj_plink('flow', curr_node) == 1:
                    path_plink.append(adj_plink)
                    curr_node = neighbor
                    
        return path_plink
    
    ## 2) Single-source single-destination maximum flow
               
    def LP_MF_formulation(self, s, t):

        # Solves the MILP: minimize c'*x
        #         subject to G*x + s = h
        #                     A*x = b
        #                     s >= 0
        #                     xi integer, forall i in I

        
        new_graph = {node: {} for node in self.nodes.values()}
        for node in self.nodes.values():
            for neighbor, plink in self.graph[node.id]['plink']:
                sd = (node == plink.source)*'SD' or 'DS'
                new_graph[node][neighbor] = getattr(plink, 'capacity' + sd)

        n = 2*len(self.plinks)
        v = len(new_graph)

        c, h = [], []
        for node in new_graph:
            for neighbor, capacity in new_graph[node].items():
                c.append(float(node == s))
                h.append(float(capacity))
                
        # flow conservation: Ax = b
        A = []
        for node_r in new_graph:
            if node_r not in (s, t):
                row = []
                for node in new_graph:
                    for neighbor in new_graph[node]:
                        row.append(
                                   1. if neighbor == node_r 
                             else -1. if node == node_r 
                              else 0.
                                   )
                A.append(row)
                
        b = np.zeros(v - 2)
        h = np.concatenate([h, np.zeros(n)])
        x = np.eye(n, n)
        G = np.concatenate((x, -1*x), axis=0).tolist()   
             
        A, G, b, c, h = map(matrix, (A, G, b, c, h))
        solsta, x = glpk.ilp(-c, G.T, h, A.T, b)

        # update the resulting flow for each node
        cpt = 0
        for node in new_graph:
            for neighbor in new_graph[node]:
                new_graph[node][neighbor] = x[cpt]
                cpt += 1
                
        # update the network physical links with the new flow value
        for plink in self.plinks.values():
            src, dest = plink.source, plink.destination
            plink.flowSD = new_graph[src][dest]
            plink.flowDS = new_graph[dest][src]

        return sum(
                   getattr(adj, 'flow' + ((s==adj.source)*'SD' or 'DS')) 
                   for _, adj in self.graph[s.id]['plink']
                   )
                   
    ## 3) Single-source single-destination minimum-cost flow
               
    def LP_MCF_formulation(self, s, t, flow):

        # Solves the MILP: minimize c'*x
        #         subject to G*x + s = h
        #                     A*x = b
        #                     s >= 0
        #                     xi integer, forall i in I

        
        new_graph = {node: {} for node in self.nodes.values()}
        for node in self.nodes.values():
            for neighbor, plink in self.graph[node.id]['plink']:
                new_graph[node][neighbor] = (plink('capacity', node),
                                             plink('cost', node))

        n = 2*len(self.plinks)
        v = len(new_graph)

        c, h = [], []
        for node in new_graph:
            for neighbor, (capacity, cost) in new_graph[node].items():
                c.append(float(cost))
                h.append(float(capacity))
                
        # flow conservation: Ax = b
        A, b = [], []
        for node_r in new_graph:
            if node_r != t:
                b.append(flow * float(node_r == s))
                row = []
                for node in new_graph:
                    for neighbor in new_graph[node]:
                        row.append(
                                   -1. if neighbor == node_r 
                              else  1. if node == node_r 
                              else  0.
                                   )
                A.append(row)
                
        h = np.concatenate([h, np.zeros(n)])
        x = np.eye(n, n)
        G = np.concatenate((x, -1*x), axis=0).tolist() 
               
        A, G, b, c, h = map(matrix, (A, G, b, c, h))
        solsta, x = glpk.ilp(c, G.T, h, A.T, b)

        # update the resulting flow for each node
        cpt = 0
        for node in new_graph:
            for neighbor in new_graph[node]:
                new_graph[node][neighbor] = x[cpt]
                cpt += 1
                
        # update the network physical links with the new flow value
        for plink in self.plinks.values():
            src, dest = plink.source, plink.destination
            plink.flowSD = new_graph[src][dest]
            plink.flowDS = new_graph[dest][src]

        return sum(
                   getattr(adj, 'flow' + ((s==adj.source)*'SD' or 'DS')) 
                   for _, adj in self.graph[s.id]['plink']
                   )
                   
    ## 4) K Link-disjoint shortest pair 
    
    def LP_LDSP_formulation(self, s, t, K):

        # Solves the MILP: minimize c'*x
        #         subject to G*x + s = h
        #                     A*x = b
        #                     s >= 0
        #                     xi integer, forall i in I

        
        self.reset_flow()
        
        all_graph = []
        for i in range(K):
            graph_K = {node: {} for node in self.nodes.values()}
            for node in graph_K:
                for neighbor, plink in self.graph[node.id]['plink']:
                    sd = (node == plink.source)*'SD' or 'DS'
                    graph_K[node][neighbor] = getattr(plink, 'cost' + sd)
            all_graph.append(graph_K)

        n = 2*len(self.plinks)
        
        c = []
        for graph_K in all_graph:
            for node in graph_K:
                for neighbor, cost in graph_K[node].items():
                    c.append(float(cost))
                
        # for the condition 0 < x_ij < 1
        h = np.concatenate([np.ones(K * n), np.zeros(K * n), np.ones(K * (K - 1) * n)])
        
        G2 = []
        for i in range(K):
            for j in range(K):
                if i != j:
                    for nodeA in all_graph[j]:
                        for neighborA in all_graph[j][nodeA]:
                            row = []
                            for k in range(K):
                                for nodeB in all_graph[k]:
                                    for neighborB in all_graph[k][nodeB]:
                                        row.append(float(k in (i, j) and 
                                                    nodeA == nodeB and
                                                    neighborA == neighborB
                                                   ))
                            G2.append(row)
                            
        id = np.eye(K * n, K * n)
        G = np.concatenate((id, -1*id, G2), axis=0).tolist()
        
        # flow conservation: Ax = b
        
        A, b = [], []
        for i in range(K):
            for node_r in self.nodes.values():
                if node_r != t:
                    row = []
                    b.append(float(node_r == s))
                    for j in range(K):
                        for node in all_graph[j]:
                            for neighbor in all_graph[j][node]:
                                row.append(
                                            -1. if neighbor == node_r and i == j 
                                    else     1. if node == node_r and i == j
                                    else     0.
                                        )
                    A.append(row)
        
        A, G, b, c, h = map(matrix, (A, G, b, c, h))
        
        binvar = set(range(n))
        solsta, x = glpk.ilp(c, G.T, h, A.T, b, B=binvar)
        print(x)
        
        # update the resulting flow for each node
        cpt = 0
        for graph_K in all_graph:
            for node in graph_K:
                for neighbor in graph_K[node]:
                    graph_K[node][neighbor] = x[cpt]
                    cpt += 1

        # update the network physical links with the new flow value
        for plink in self.plinks.values():
            src, dest = plink.source, plink.destination
            plink.flowSD = max(graph_K[src][dest] for graph_K in all_graph)
            plink.flowDS = max(graph_K[dest][src] for graph_K in all_graph)
            
        return sum(x)
        
    ## IP network cost optimization: Weight Setting Problem
    
    
    
    # compute the network congestion ratio of an autonomous system
    # it is defined as max( link bw / link capacity for all links):
    # it is the maximum utilization ratio among all AS links.
    # we also use this function to retrieve the argmax, that is, 
    # the physical link with the highlight bandwidth / capacity ratio.
    def ncr_computation(self, AS_links):
        # ct_id is the index of the congested plink bandwidth in AS_links
        # cd indicates which is the congested direction: SD or DS
        ncr, ct_id, cd = 0, None, None
        for idx, plink in enumerate(AS_links):
            for direction in ('SD', 'DS'):
                tf, cap = 'traffic' + direction, 'capacity' + direction 
                curr_ncr = getattr(plink, tf) / getattr(plink, cap)
                if curr_ncr > ncr:
                    ncr = curr_ncr
                    ct_id = idx
                    cd = direction
        return ncr, ct_id, cd
        
    # 2) Tabu search heuristic
                   
    def WSP_TS(self, AS):
        
        AS_links = list(AS.pAS['link'])
        
        # a cost assignment solution is a vector of 2*n value where n is
        # the number of physical links in the AS, because each physical link
        # has two costs:
        # one per direction (SD and DS).
        n = 2*len(AS_links)
        
        iteration_nb = 50
        
        # the tabu list is an empty: it will contain all the solutions, so that
        # we don't evaluate a solution more than once (we don't go 'backward')
        tabu_list = []
        
        # the current optimal solution found
        best_solution = None
        
        # for each solution, we compute the 'network congestion ratio':
        # best_ncr is the best network congestion ratio that has been found
        # so far, i.e the network congestion ratio of the best solution. 
        best_ncr = float('inf')
        
        # we store the cost value in the flow parameters, since we'll change
        # the links' costs to evaluate each solution
        # at the end, we will revert the cost to their original value
        for plink in AS.pAS['link']:
            plink.flowSD = plink.costSD
            plink.flowDS = plink.costDS
            
        generation_size = 10
        best_candidates = []
        
        for i in range(generation_size):
            print(i)
            curr_solution = [random.randint(1, n) for _ in range(n)]
                
            # we assign the costs to the physical links
            for id, cost in enumerate(curr_solution):
                setattr(AS_links[id//2], 'cost' + ('DS'*(id%2) or 'SD'), cost)
                
            # create the routing tables with the newly allocated costs,
            # route all traffic flows and find the network congestion ratio
            self.routing_table_creation()
            self.path_finder()
            
            curr_ncr, *_ = self.ncr_computation(AS_links)
            best_candidates.append((curr_ncr, curr_solution)) 
                    
        best_candidates = nsmallest(5, best_candidates)
                    
        for i, (_, curr_solution) in enumerate(best_candidates):
            print(i)
            
            if curr_solution in tabu_list:
                continue
                
            # we create an cost assignment and add it to the tabu list
            tabu_list.append(curr_solution)
            
            # we assign the costs to the physical links
            for id, cost in enumerate(curr_solution):
                setattr(AS_links[id//2], 'cost' + ('DS'*(id%2) or 'SD'), cost)
            
            self.route()
            
            # if we have to look for the most congested physical link more than 
            # C_max times, and still can't have a network congestion 
            # ratio lower than best_ncr, we stop
            C_max, C = 10, 0
            local_best_ncr = float('inf')
            
            while True:
                self.route()
                    
                curr_ncr, ct_id, cd = self.ncr_computation(AS_links)

                # update the best solution found if the network congestion ratio
                # is the lowest one found so far
                if curr_ncr < local_best_ncr:
                    print(curr_ncr)
                    C = 0
                    local_best_ncr = curr_ncr
                    if curr_ncr < best_ncr:
                        best_ncr = curr_ncr
                        best_solution = curr_solution[:]
                else:
                    C += 1
                    if C == C_max:
                        print(best_ncr)
                        break
                    
                # we store the bandwidth of the physical link with the highest
                # congestion (in the congested direction)
                initial_bw = getattr(AS_links[ct_id], 'traffic' + cd)
                    
                # we'll increase the cost of the congested physical link, until
                # at least one traffic is rerouted (in such a way that it will
                # no longer use the congested physical link)
                for k in range(5):
                    #print(k)
                    AS_links[ct_id].__dict__['cost' + cd] += n // 5
                    # we update the solution being evaluated and append
                    # it to the tabu list
                    curr_solution[ct_id*2 + (cd == 'DS')] += n // 5
                    
                    tabu_list.append(curr_solution)
                    
                    self.route()
                    
                    new_bw = getattr(AS_links[ct_id], 'traffic' + cd)
                    
                    if new_bw != initial_bw:
                        break
                else:
                    C = C_max - 1
                

        for id, cost in enumerate(best_solution):
            setattr(AS_links[id//2], 'cost' + ('DS'*(id%2) or 'SD'), cost)
        self.route()
        ncr, ct_id, cd = self.ncr_computation(AS_links)
        print(ncr)
        
    ## Optical networks: routing and wavelength assignment
    
    def RWA_graph_transformation(self, name=None):
        
        # we compute the path of all traffic physical links
        self.path_finder()
        graph_project = self.view.controller.add_project(name)

        # in the new graph, each node corresponds to a traffic path
        # we create one node per traffic physical link in the new view            
        visited = set()
        # tl stands for traffic physical link
        for tlA in self.traffics.values():
            for tlB in self.traffics.values():
                if tlB not in visited and tlA != tlB:
                    if set(tlA.path) & set(tlB.path):
                        nA, nB = tlA.name, tlB.name
                        name = '{} - {}'.format(nA, nB)
                        graph_project.network.lf(
                                source = graph_project.network.nf(
                                                    name = nA,
                                                    subtype = 'optical switch'
                                                    ),
                                destination = graph_project.network.nf(
                                                    name = nB,
                                                    subtype = 'optical switch'
                                                    ),
                                name = name
                                )
            visited.add(tlA)
                            
        graph_project.current_view.refresh_display()
        return graph_project
        
    def largest_degree_first(self):
        # we color the transformed graph by allocating colors to largest
        # degree nodes:
        # 1) we select the largest degree uncolored optical switch
        # 2) we look at the adjacent vertices and select the minimum indexed
        # color not yet used by adjacent vertices
        # 3) when everything is colored, we stop
        
        # we will use a dictionary that binds optical switch to the color it uses.
        optical_switch_color = dict.fromkeys(self.ftr('node', 'optical switch'), None)
        # and a list that contains all vertices that we have yet to color
        uncolored_nodes = list(optical_switch_color)
        # we will use a function that returns the degree of a node to sort
        # the list in ascending order
        uncolored_nodes.sort(key = lambda node: len(self.graph[node.id]['plink']))
        # and pop nodes one by one
        while uncolored_nodes:
            largest_degree = uncolored_nodes.pop()
            # we compute the set of colors used by adjacent vertices
            colors = set(optical_switch_color[neighbor] for neighbor, _ in
                                    self.graph[largest_degree.id]['plink'])
            # we find the minimum indexed color which is available
            min_index = [i in colors for i in range(len(colors) + 1)].index(0)
            # and assign it to the current optical switch
            optical_switch_color[largest_degree] = min_index
            
        number_lambda = max(optical_switch_color.values()) + 1
        return number_lambda
        
    def LP_RWA_formulation(self, K=10):

        # Solves the MILP: minimize c'*x
        #         subject to G*x + s = h
        #                     A*x = b
        #                     s >= 0
        #                     xi integer, forall i in I

        # we note x_v_wl the variable that defines whether wl is used for 
        # the path v (x_v_wl = 1) or not (x_v_wl = 0)
        # we construct the vector of variable the following way:
        # x = [x_1_0, x_2_0, ..., x_V_0, x_1_1, ... x_V-1_K-1, x_V_K-1]
        # that is, [(x_v_0) for v in V, ..., (x_v_K) for wl in K]
        
        # V is the total number of path (i.e the total number of physical links
        # in the transformed graph)
        V, T = len(self.nodes), len(self.plinks)
        
        # for the objective function, which must minimize the sum of y_wl, 
        # that is, the number of wavelength used
        c = np.concatenate([np.zeros(V * K), np.ones(K)])
        
        # for a given path v, we must have sum(x_v_wl for wl in K) = 1
        # which ensures that each optical path uses only one wavelength
        # for each path v, we must create a vector with all x_v_wl set to 1
        # for the path v, and the rest of it set to 0.
        A = []
        for path in range(V):
            row = [float(K * path <= i < K * (path + 1)) for i in range(V * K)] 
            row += [0.] * K
            A.append(row)
            
        b = np.ones(V)
        
        G2 = []
        for i in range(K):
            for plink in self.plinks.values():
                p_src, p_dest = plink.source, plink.destination
                # we want to ensure that paths that have at least one physical link in 
                # common are not assigned the same wavelength.
                # this means that x_v_src_i + x_v_dest_i <= y_i
                row = []
                # vector of x_v_wl: we set x_v_src_i and x_v_dest_i to 1
                for path in self.nodes.values():
                    for j in range(K):
                        row.append(float(
                                         (path == p_src or path == p_dest)
                                                        and
                                                       i == j
                                         )
                                   )
                # we continue filling the vector with the y_wl
                # we want to have x_v_src_i + x_v_dest_i - y_i <= 0
                # hence the 'minus' sign instead of float
                for j in range(K):
                    row.append(-float(i == j))
                G2.append(row)
        # G2 size should be K * T (rows) x K * (V + 1) (columns)

        # finally, we want to ensure that wavelength are used in 
        # ascending order, meaning that y_wl >= y_(wl + 1) for wl 
        # in [0, K-1]. We can rewrite it y_(wl + 1) - y_wl <= 0
        G3 = []
        for i in range(1, K):
            row_wl = [float(
                            (i == wl)
                                or 
                            -(i == wl + 1)
                            )
                        for wl in range(K)
                      ]
            final_row = np.concatenate([np.zeros(V * K), row_wl])
            G3.append(final_row)
        # G3 size should be K - 1 (rows) x K * (V + 1) (columns)

        h = np.concatenate([
                            # x_v_src_i + x_v_dest_i - y_i <= 0
                            np.zeros(K * T),
                            # y_(wl + 1) - y_wl <= 0
                            np.zeros(K - 1)
                            ])

        G = np.concatenate((G2, G3), axis=0).tolist()
        A, G, b, c, h = map(matrix, (A, G, b, c, h))
    
        binvar = set(range(K * (V + 1)))
        solsta, x = glpk.ilp(c, G.T, h, A.T, b, B=binvar)
        
        warnings.warn(str(int(sum(x[-K:]))))
        return int(sum(x[-K:]))
        
    ## Graph generation functions
                
    ## 1) Tree generation
                
    def tree(self, n, subtype):
        for i in range(2**n-1):
            n1, n2, n3 = str(i), str(2*i+1), str(2*i+2)
            source = self.nf(name = n1, subtype = subtype)
            destination = self.nf(name = n2, subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
            source = self.nf(name = n1, subtype = subtype)
            destination = self.nf(name = n3, subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
            
    ## 2) Star generation
            
    def star(self, n, subtype):
        nb_node = self.cpt_node + 1
        for i in range(n):
            n1, n2 = str(nb_node), str(nb_node+1+i)
            source = self.nf(name = n1, subtype = subtype)
            destination = self.nf(name = n2, subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
            
    ## 3) Full-meshed network generation
            
    def full_mesh(self, n, subtype):
        nb_node = self.cpt_node + 1
        for i in range(n):
            for j in range(i):
                n1, n2 = str(nb_node+j), str(nb_node+i)
                source = self.nf(name = n1, subtype = subtype)
                destination = self.nf(name = n2, subtype = subtype)
                yield source
                yield destination
                yield self.lf(source=source, destination=destination)
                
    ## 4) Ring generation
                
    def ring(self, n, subtype):
        nb_node = self.cpt_node + 1
        for i in range(n):
            n1, n2 = str(nb_node+i), str(nb_node+(1+i)%n)
            source = self.nf(name = n1, subtype = subtype)
            destination = self.nf(name = n2, subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
                    
    ## 5) Square tiling generation
            
    def square_tiling(self, n, subtype):
        for i in range(n**2):
            n1, n2, n3 = str(i), str(i-1), str(i+n)
            if i-1 > -1 and i%n:
                source = self.nf(name = n1, subtype = subtype)
                destination = self.nf(name = n2, subtype = subtype)
                yield source
                yield destination
                yield self.lf(source=source, destination=destination)
            if i+n < n**2:
                source = self.nf(name = n1, subtype = subtype)
                destination = self.nf(name = n3, subtype = subtype)
                yield source
                yield destination
                yield self.lf(source=source, destination=destination)
                    
    ## 6) Hypercube generation
            
    def hypercube(self, n, subtype):
        # we create a n-dim hypercube by connecting two (n-1)-dim hypercubes
        i = 0
        graph_nodes = [self.nf(name=str(0), subtype=subtype)]
        graph_plinks = []
        while i < n+1:
            for k in range(len(graph_nodes)):
                # creation of the nodes of the second hypercube
                graph_nodes.append(
                                   self.nf(
                                           name = str(k+2**i), 
                                           subtype = subtype
                                           )
                                   )
            for plink in graph_plinks[:]:
                # connection of the two hypercubes
                source, destination = plink.source, plink.destination
                n1 = str(int(source.name) + 2**i)
                n2 = str(int(destination.name) + 2**i)
                graph_plinks.append(
                                   self.lf(
                                           source = self.nf(name = n1), 
                                           destination = self.nf(name = n2)
                                           )
                                   )
            for k in range(len(graph_nodes)//2):
                # creation of the physical links of the second hypercube
                graph_plinks.append(
                                   self.lf(
                                           source = graph_nodes[k], 
                                           destination = graph_nodes[k+2**i]
                                           )
                                   )
            i += 1
        yield from graph_nodes
        yield from graph_plinks
                    
    ## 7) Generalized Kneser graph
    
    def kneser(self, n, k, subtype):
        # we keep track of what set we've seen to avoid having
        # duplicated edges in the graph, with the 'already_done' set
        already_done = set()
        for setA in map(set, combinations(range(1, n), k)):
            already_done.add(frozenset(setA))
            for setB in map(set, combinations(range(1, n), k)):
                if setB not in already_done and not setA & setB:
                    source = self.nf(name = str(setA), subtype = subtype)
                    destination = self.nf(name = str(setB), subtype = subtype)
                    yield source
                    yield destination
                    yield self.lf(source=source, destination=destination)
                            
    ## 8) Generalized Petersen graph
    
    def petersen(self, n, k, subtype):
        # the petersen graph is made of the vertices (u_i) and (v_i) for 
        # i in [0, n-1] and the edges (u_i, u_i+1), (u_i, v_i) and (v_i, v_i+k).
        # to build it, we consider that v_i = u_(i+n).
        for i in range(n):
            # (u_i, u_i+1) edges
            source = self.nf(name = str(i), subtype = subtype)
            destination = self.nf(name = str((i + 1)%n), subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
            # (u_i, v_i) edges
            source = self.nf(name = str(i), subtype = subtype)
            destination = self.nf(name = str(i+n), subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
            # (v_i, v_i+k) edges
            source = self.nf(name = str(i+n), subtype = subtype)
            destination = self.nf(name = str((i+n+k)%n + n), subtype = subtype)
            yield source
            yield destination
            yield self.lf(source=source, destination=destination)
                    
    ## Multiple object creation
    
    def multiple_nodes(self, n, subtype):
        nb_nodes = self.cpt_node + 1
        for k in range(n):
            yield self.nf(name = str(k + nb_nodes), subtype = subtype)
            
    def multiple_links(self, source_nodes, destination_nodes):
        # create a link between the destination node and all source nodes
        for src_node in source_nodes:
            for dest_node in destination_nodes:
                if src_node != dest_node:
                    yield self.lf(source=src_node, destination=dest_node)
                
    ## Configuration
    
    def build_router_configuration(self, node):
        # initialization
        # yield 'enable'
        yield 'configure terminal'
        
        # configuration of the loopback interface
        # yield 'interface Loopback0'
        # yield 'ip address {ip} 255.255.255.255'.format(ip=node.ip_address) 
        # yield 'exit'
        
        for _, sr in self.gftr(node, 'route', 'static route', False):
            subnetwork, mask = sr.dst_sntw.split('/')
            mask = tomask(int(mask))
            yield ' '.join(('ip route ', subnetwork, mask, sr.nh_ip))
        
        for neighbor, adj_plink in self.graph[node.id]['plink']:
            interface = adj_plink('interface', node)
            ip = interface.ip_address
            mask = interface.subnet_mask
            
            yield 'interface ' + str(interface)
            yield 'ip address {ip} {mask}'.format(ip=ip.ip_addr, mask=ip.mask)
            yield 'no shutdown'
            yield 'exit'
            
            if any(AS.AS_type == 'OSPF' for AS in adj_plink.AS):
                cost = adj_plink('cost', node)
                if cost != 1:
                    yield 'ip ospf cost ' + cost
                    
            # IS-IS is configured both in 'config-router' mode and on the 
            # interface itself: the code is set here so that the user doesn't
            # have the exit the interace, then come back to it for IS-IS.
            for AS in node.AS:
                
                # we configure isis only if the neighbor 
                # belongs to the same AS.
                if AS in neighbor.AS and AS.AS_type == 'ISIS':
                    
                    node_area ,= node.AS[AS]
                    in_backbone = node_area.name == 'Backbone'
                    
                    # activate IS-IS on the interface
                    yield 'ip router isis'
                                                        
                    # we need to check what area the neighbor belongs to.
                    # If it belongs to the node's area, the interface is 
                    # configured as L1 with circuit-type, else with L2.            
                    neighbor_area ,= neighbor.AS[AS]
                    
                    # we configure circuit-type as level 2 if the routers
                    # belong to different areas, or they both belong to
                    # the backbone
                    l2 = node_area != neighbor_area or in_backbone
                    cct_type = 'level-2' if l2 else 'level-1'
                    yield 'isis circuit-type ' + cct_type
            
        for AS in node.AS:
            
            if AS.AS_type == 'RIP':
                yield 'router rip'
                
                for _, adj_plink in self.graph[node.id]['plink']:
                    interface = adj_plink('interface', node)
                    if adj_plink in AS.pAS['link']:
                        ip = interface.ip_address
                        
                        yield 'network ' + ip.ip_addr
                    else:
                        yield 'passive-interface ' + interface.name
                
            elif AS.AS_type == 'OSPF':
                
                yield 'router ospf 1'
                
                for _, adj_plink in self.graph[node.id]['plink']:
                    interface = adj_plink('interface', node)
                    if adj_plink in AS.pAS['link']:
                        ip = interface.ip_address
                        plink_area ,= adj_plink.AS[AS]
                        yield ' '.join((
                                        'network', 
                                        ip.ip_addr, 
                                        '0.0.0.3', 
                                        'area', 
                                        str(plink_area.id)
                                        )) 
                            
                    else:
                        if_name = interface.name
                        yield 'passive-interface ' + if_name
                        
                if AS.exit_point == node:
                    yield 'default-information originate'
                
            elif AS.AS_type == 'ISIS':
                
                # we need to know:
                # - whether the node is in the backbone area (L1/L2 or L2) 
                # or a L1 area
                # - whether the node is at the edge of its area (L1/L2)
                node_area ,= node.AS[AS]
                in_backbone = node_area.name == 'Backbone'
                level = 'level-1-2' if node in AS.border_routers else (
                        'level-2' if in_backbone else 'level-1')
                
                # An IS-IS NET (Network Entity Title) is made up of:
                    # - AFI must be 1 byte
                    # - Area ID can be 0 to 12 bytes long
                    # - System ID must be 6 bytes long
                    # - SEL must be 1 byte
                    
                # The AFI, or the Authority & Format Identifier.
                # In an IP-only environment, this number has no meaning 
                # separate from the Area ID it Most vendors and operators 
                # tend to stay compliant with the defunct protocols by 
                # specifying an AFI of “49”. 
                # We will stick to this convention.
                
                # Area ID’s function just as they do in OSPF.
                
                # System ID can be anything chosen by the administrator, 
                # similarly to an OSPF Router ID. However, best practice 
                # with NETs is to keep the configuration as simple as 
                # humanly possible.
                # We will derive it from the router's loopback address
                    
                AFI = '49.' + str(format(node_area.id, '04d'))
                sid = '.'.join((format(int(n), '03d') for n in node.ip_address.split('.')))
                net = '.'.join((AFI, sid, '00'))
            
                yield 'router isis'
                yield 'net ' + net           
                yield 'is-type ' + level                        
                yield 'passive-interface Loopback0'
                yield 'exit'
        
    def build_switch_configuration(self, node):
        # initialization
        yield 'enable'
        yield 'configure terminal'
        
        # create all VLAN on the switch
        for AS in node.AS:
            if AS.AS_type == 'VLAN':
                for VLAN in node.AS[AS]:
                    yield 'vlan ' + VLAN.id
                    yield 'name ' + VLAN.name
                    yield 'exit'
        
        for _, adj_plink in self.graph[node.id]['plink']:
            interface = adj_plink('interface', node)
            yield 'interface ' + interface
                                    
            for AS in adj_plink.AS:
                
                # VLAN configuration
                if AS.AS_type == 'VLAN':
                    
                    # if there is a single VLAN, the link is an access link
                    if len(adj_plink.AS[AS]) == 1:
                        # retrieve the unique VLAN the link belongs to
                        unique_VLAN ,= adj_plink.AS[AS]
                        yield 'switchport mode access'
                        yield 'switchport access vlan ' + unique_VLAN.id
                                
                    else:
                        # there is more than one VLAN, the link is a trunk
                        yield 'switchport mode trunk'
                        # finds all VLAN IDs
                        VLAN_IDs = map(lambda vlan: str(vlan.id), adj_plink.AS[AS])
                        # allow them on the trunk
                        yield 'switchport trunk allowed vlan add ' + ','.join(VLAN_IDs)
                        
        yield 'end'
            