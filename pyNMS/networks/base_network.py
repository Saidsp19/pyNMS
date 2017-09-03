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

from objects.objects import *
from collections import defaultdict
from math import sqrt

class BaseNetwork(object):
    
    def __init__(self, view):
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
                   }
                   
        self.view = view
        self.graph = defaultdict(lambda: defaultdict(set))
        self.cpt_link = self.cpt_node = self.cpt_AS = 1
        # useful for tests and listbox when we want to retrieve an object
        # based on its name. The only object that needs changing when a object
        # is renamed by the user.
        self.name_to_id = {}

        # set of all objects in failure: this parameter is used for
        # link dimensioning and failure simulation
        self.failed_obj = set()

    # function filtering pn to retrieve all objects of given subtypes
    def ftr(self, type, *sts):
        keep = lambda r: r.subtype in sts
        if type == 'interface':
            return filter(keep, self.pn[type])
        else:
            return filter(keep, self.pn[type].values())
        
    # function filtering graph to retrieve all links of given subtypes
    # attached to the source node. 
    # if ud (undirected) is set to True, we retrieve all links of the 
    # corresponding subtypes, else we check that 'src' is the source
    def gftr(self, src, type, *sts, ud=True):
        keep = lambda r: r[1].subtype in sts and (ud or r[1].source == src)
        return filter(keep, self.graph[src.id][type])
          
    # 'lf' is the link factory. Creates or retrieves any type of link
    def lf(self, subtype='ethernet link', id=None, name=None, **kwargs):
        link_type = subtype_to_type[subtype]
        # creation link in the s-d direction if no link at all yet
        if not id:
            if name in self.name_to_id:
                link = self.pn[link_type][self.name_to_id[name]]
                link.update_properties(kwargs)
                return link
            s, d = kwargs['source'], kwargs['destination']
            id = self.cpt_link
            if not name:
                name = subtype + str(self.cpt_link)
            kwargs.update({'id': id, 'name': name})
            new_link = link_class_with_vc[subtype](**kwargs)
            self.name_to_id[name] = id
            self.pn[link_type][id] = new_link
            self.graph[s.id][link_type].add((d, new_link))
            self.graph[d.id][link_type].add((s, new_link))
            if subtype in ('ethernet link', 'optical link'):
                self.interfaces |= {new_link.interfaceS, new_link.interfaceD}
            self.cpt_link += 1
        return self.pn[link_type][id]
        
    # 'nf' is the node factory. Creates or retrieves any type of nodes
    def nf(self, subtype='router', id=None, **kwargs):
        if 'name' not in kwargs:
            name = subtype + str(self.cpt_node)
            kwargs['name'] = name
        else:
            if kwargs['name'] in self.name_to_id:
                node = self.nodes[self.name_to_id[kwargs['name']]]
                node.update_properties(kwargs)
                return node
        id = self.cpt_node
        kwargs['id'] = id
        self.nodes[id] = node_class[subtype](**kwargs)
        self.name_to_id[kwargs['name']] = id
        self.cpt_node += 1
        return self.nodes[id]
        
    # 'of' is the object factory: returns a link or a node from its name
    def of(self, name, _type):
        if _type == 'node':
            return self.nf(name=name)
        else:
            return self.lf(name=name)
            
    ## Conversion methods and property -> type mapping
    
    def objectizer(self, str_property, value):
        if value == 'None': 
            return None
        elif str_property in property_classes:
            property = property_classes[str_property]
            if property.conversion_needed:
                value = getattr(self, property.converter)(value)
            return value
        # if the property doesn't exist, we consider it is a string
        else:
            return value
                        
    def mass_objectizer(self, properties, values):
        kwargs = {}
        for property, value in zip(properties, values):
            kwargs[property] = self.objectizer(property, value)
        return kwargs
    
    # methods used to convert a string to an object 
    
    # convert a node name to a node
    def convert_node(self, node_name):
        return self.nf(name=node_name)
    
    # convert a link name to a node
    def convert_link(self, link_name, subtype='ethernet link'):
        return self.lf(name=link_name, subtype=subtype)
        
    # convert an iterable of strings representing nodes, to a generator of nodes
    def convert_nodes(self, nodes):
        return map(self.convert_node, nodes)
    
    # convert a string representing a set of nodes, to an actual set of nodes
    def convert_node_set(self, node_set):
        return set(map(self.convert_node, eval(node_set)))
    
    # convert a string representing a list of nodes, to an actual list of nodes
    def convert_node_list(self, node_list):
        return list(map(self.convert_node, eval(node_list)))
        
    # convert an iterable of strings representing links, to a generator of links
    def convert_links(self, links):
        return map(self.convert_link, links)
    
    # convert a string representing a set of links, to an actual set of links
    def convert_link_set(self, link_set, subtype='ethernet link'):
        convert = lambda link: self.convert_link(link, subtype)
        return set(map(convert, eval(link_set)))
    
    # convert a string representing a list of links, to an actual list of links
    def convert_link_list(self, link_list, subtype='ethernet link'):
        convert = lambda link: self.convert_link(link, subtype)
        return list(map(convert, eval(link_list)))
            
    def erase_network(self):
        self.graph.clear()
        for dict_of_objects in self.pn.values():
            dict_of_objects.clear()
            
    def remove_node(self, node):
        self.nodes.pop(self.name_to_id.pop(node.name))
        # retrieve adj links to delete them 
        dict_of_adj_links = self.graph.pop(node.id, {})
        for type_link, adj_obj in dict_of_adj_links.items():
            for neighbor, adj_link in adj_obj:
                yield adj_link

    def remove_link(self, link):
        # if it is a physical link, remove the link's interfaces from the model
        if link.type == 'plink':
            self.interfaces -= {link.interfaceS, link.interfaceD}
        # remove the link itself from the model
        self.graph[link.source.id][link.type].discard((link.destination, link))
        self.graph[link.destination.id][link.type].discard((link.source, link))
        self.pn[link.type].pop(self.name_to_id.pop(link.name, None), None)
            
    def is_connected(self, nodeA, nodeB, link_type, subtype=None):
        if not subtype:
            return any(n == nodeA for n, _ in self.graph[nodeB.id][link_type])
        else:
            return any(n == nodeA for n, _ in self.gftr(nodeB, link_type, subtype))
        
    # given a node, retrieves nodes attached with a link which subtype 
    # is in sts
    def neighbors(self, node, *subtypes):
        for subtype in subtypes:
            for neighbor, _ in self.graph[node.id][subtype]:
                yield neighbor
                
    def all_nodes(self):
        yield from self.nodes.values()
                
    def all_links(self):
        for type in link_type:
            yield from self.pn[type].values()
                
    # given a node, retrieves all attached links    
    def attached_links(self, node):
        for type in link_type:
            # we make it a set as this function is used, among other things,
            # for the deletion of links and a set cannot change size during
            # iteration
            for _, link in set(self.graph[node.id][type]):
                yield link
        
    def number_of_links_between(self, nodeA, nodeB):
        return sum(
                   n == nodeB 
                   for _type in self.link_type 
                   for n, _ in self.graph[nodeA.id][_type]
                   )
        
    def links_between(self, nodeA, nodeB, _type='all'):
        if _type == 'all':
            for type in link_type:
                for neighbor, link in self.graph[nodeA.id][type]:
                    if neighbor == nodeB:
                        yield link
        else:
            for neighbor, link in self.graph[nodeA.id][_type]:
                if neighbor == nodeB:
                    yield link
                                                
    ## Graph functions
    
    def bfs(self, source):
        visited = set()
        layer = {source}
        while layer:
            temp = layer
            layer = set()
            for node in temp:
                if node not in visited:
                    visited.add(node)
                    for neighbor, _ in self.graph[node.id]['plink']:
                        layer.add(neighbor)
                        yield neighbor
    
    def connected_components(self):
        visited = set()
        for node in self.nodes.values():
            if node not in visited:
                new_comp = set(self.bfs(node))
                visited.update(new_comp)
                yield new_comp
        