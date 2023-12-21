"""
Convert workflow to prompt api
"""

import logging
import json
from typing import Any, Callable, Union, Optional, Dict, List
from dataclasses import dataclass
import pathlib
from comfyui_utils import comfy
from dot4dict import dotdict
from enum import Enum

logger= logging.getLogger("comfyui_utils").getChild("workflow2prompt")

_cached_object_infos: Dict[str, Dict]= {}

async def _get_object_infos(class_type: str, comfyapi_client: comfy.ComfyAPI) -> Optional[dict]:
    global _cached_object_infos
    if len(_cached_object_infos) == 0:
        logger.info("Retrieve all object info from server")
        results= await comfyapi_client.get_all_object_info()
        _cached_object_infos= results
    if class_type in _cached_object_infos.keys():
        logger.debug("Object info %s served from local cache", class_type)
        return _cached_object_infos[class_type]
    # logger.info("Retrieve object info on class type %s from ComfyUI", class_type)
    # infos= await comfyapi_client.get_object_info(class_type=class_type)
    # if infos is not None:
    #     logger.debug("Object info %s retrieved from ComfyUI", class_type)
    #     _cached_object_infos[class_type]= infos
    else:
        logger.debug("Unable to retrieve object info %s from ComfyUI", class_type)
    return None

class Link:
    id: int
    src_node: int
    src_pos: int
    dst_node: int
    dst_pos: int
    datatype: str
    final_src_node: int
    final_src_pos: int
    
    def __init__(self, id: int, src_node: int, src_pos, dst_node: int, dst_pos: int, datatype: str):
        self.id= id
        self.src_node= src_node
        self.src_pos= src_pos
        self.dst_node= dst_node
        self.dst_pos= dst_pos
        self.datatype= datatype
        self.final_src_node= src_node
        self.final_src_pos= src_pos

    def __str__(self):
        if (self.src_node != self.final_src_node):
            return f"Link {self.id:4d}. {self.src_node}:{self.src_pos} ({self.final_src_node}:{self.final_src_pos}) -> {self.dst_node}:{self.dst_pos}."
        
        return f"Link {self.id:4d}. {self.src_node}:{self.src_pos} -> {self.dst_node}:{self.dst_pos}."

    def __repr__(self):       
        return f"Link {self.id:4d}. {self.src_node}:{self.src_pos} ({self.final_src_node}:{self.final_src_pos}) -> {self.dst_node}:{self.dst_pos}"


class NodeInputType(Enum):
    WIDGET= "Widget" 
    HIDDEN_WIDGET= "Hidden Widget"
    LINK= "Link"

_node_extra_widgets: dict= {
    "seed": ["control_after_generate"],
    "noise_seed": ["control_after_generate"],
}

_node_skip_widgets: list= ["insert_saved"]
    
@dataclass
class NodeInput:
    input_name: str
    index: int
    value: Any
    input_type: NodeInputType
    widget_index: Optional[int]
    
class Node:
    id: int
    _data: dotdict
    inputs: List[NodeInput]
    widgets: dotdict[str, NodeInput]
    class_type: str
    disabled: bool
    object_infos: Optional[Dict]
    initialized: bool
    comfyapi_client: comfy.ComfyAPI
    
    def __init__(self, json_input: dotdict, comfyapi_client: comfy.ComfyAPI):
        self._data= json_input
        self.comfyapi_client= comfyapi_client
        if "id" not in json_input.keys():
            raise ValueError("Invalid json_input, id is missing")
        self.id= self._data.id
        self.class_type= self._data["type"]
        self.disabled= self._data.mode in [2,4]
        self.inputs= None
        self.initialized= False
        
    @classmethod
    async def create(cls, json_input: dotdict, links: Dict[int, Link], comfyapi_client: comfy.ComfyAPI):
        node= cls(json_input, comfyapi_client)
        node.inputs= []
        node.widgets= dotdict()
        node.object_infos= await _get_object_infos(node.class_type, comfyapi_client)

        #Do no try to parse further info, this node is disabled
        if node.disabled:
            return node
        
        node._hidden_inputs: dotdict()
        possible_hidden_values= 0
        node._info_input_names: list= []
        if node.object_infos is not None:
            for section, val in node.object_infos["input"].items():
                if section == "hidden":
                    continue
                for k in val.keys():
                    node._info_input_names.append(k)
                    if k in _node_extra_widgets.keys():
                        possible_hidden_values+= len(_node_extra_widgets[k])
        else:
            node.disabled= True    
            if "eroute" in node.class_type:
                logger.debug(f"DISABLING Node {node.id} of type {node.class_type}")
            else:
                logger.warning(f"DISABLING Node {node.id} of type {node.class_type}")
            return node

        node_inputs= node._data.inputs if node._data.inputs is not None else []
        widgets_values= node._data.widgets_values if node._data.widgets_values is not None else []
        #Each input in the model must be in the workflow as an input or a widget value
        #hidden values could be hard to handle
        if (len(node._info_input_names) > (len(node_inputs) + len(widgets_values))
            or len(node._info_input_names) < (len(node_inputs) + len(widgets_values) - possible_hidden_values)):
            logger.debug(f"Node {node.id} of type {node.class_type} has {len(node._info_input_names)} infos in the model and {(len(node_inputs) + len(widgets_values))} in the workflow and possibly {possible_hidden_values} hidden values.")
        widget_index= 0
        for input_index, input_name in enumerate(node._info_input_names):
            for input in node_inputs:
                if input.name == input_name:
                    if input_name in _node_skip_widgets:
                        logger.warning(f"Node {node.id} skipping widget {input_name}")
                        continue
                    linkvalue= None
                    if input.link in links.keys():
                        linkvalue= links[input.link]
                    elif input.link is not None:
                        raise ValueError("Link %r is not in the link table", input.link)
                    w_index= None
                    widget_value= None
                    if input.widget is not None and widget_index < len(widgets_values):
                        widget_value= widgets_values[widget_index] 
                        w_index= widget_index
                        widget_index+= 1
                    elif input.widget is not None:
                        widget_value= None
                        logger.warning(f"Out of widget_values Node {node.id} LINK input {input_name}")
                    node_input= NodeInput(
                        input_name=input_name,
                        index=input_index,
                        value=linkvalue,
                        input_type=NodeInputType.LINK,
                        widget_index=w_index
                    )
                    node.inputs.append(node_input)                     
                    if widget_value is not None:
                        node_input.widget_value= widget_value
                        extra_inputs= _node_extra_widgets.get(input_name, [])
                        for extra_input in extra_inputs:
                            if widget_index < len(widgets_values):
                                widget_value= widgets_values[widget_index] 
                            else:
                                widget_value= None
                                logger.warning(f"Out of widget_values Node {node.id} input {input_name}")
                                continue
                            node_input= NodeInput(
                                input_name=extra_input,
                                index=-1,
                                value=widget_value,
                                input_type=NodeInputType.HIDDEN_WIDGET,
                                widget_index=widget_index
                            )
                            node.inputs.append(node_input)
                            widget_index+= 1                        
                    break
            else:
                if widget_index < len(widgets_values):
                    widget_value= widgets_values[widget_index] 
                else:
                    widget_value= None
                    logger.warning(f"Out of widget_values Node {node.id} input {input_name}")
                    continue
                node_input= NodeInput(
                    input_name=input_name,
                    index=input_index,
                    value=widget_value,
                    input_type=NodeInputType.WIDGET,
                    widget_index=widget_index
                )
                node.inputs.append(node_input)
                node.widgets[input_name]= node_input
                widget_index+= 1
                extra_inputs= _node_extra_widgets.get(input_name, [])
                for extra_input in extra_inputs:
                    if node.class_type == "SDParameterGenerator":
                        continue
                    if widget_index < len(widgets_values):
                        widget_value= widgets_values[widget_index] 
                    else:
                        widget_value= None
                        logger.warning(f"Out of widget_values Node {node.id} input {input_name}")
                        continue
                    node_input= NodeInput(
                        input_name=extra_input,
                        index=-1,
                        value=widget_value,
                        input_type=NodeInputType.HIDDEN_WIDGET,
                        widget_index=widget_index
                    )
                    node.inputs.append(node_input)
                    widget_index+= 1
        if widget_index !=  len(widgets_values):
            logger.warning(f"Node {node.id} ({node.class_type}) {len(widgets_values)} values expected but {widget_index} provided")
        node.initialized= True
        return node            

class Workflow:
    _data: dotdict
    filepath: pathlib.Path
    nodes: Dict[int, Node]
    links: Dict[int, Link]
    object_infos: Dict[str, Dict]
    comfyapi_client: comfy.ComfyAPI
    
    def __init__(self, path: pathlib.Path, comfyapi_client: comfy.ComfyAPI):
        self.comfyapi_client= comfyapi_client
        if not path.is_file():
            raise ValueError(f"File {path.resolve()} does not exists")
        self.filepath= path
        
        try:
            with open(path, 'r', encoding='cp1251') as file:
                self._data= json.load(file, object_hook=dotdict)
        except Exception as e:
            logger.critical("Error loading json file %s : %s", path.name, e)
            raise e
        
    @classmethod
    async def load(cls, path: pathlib.Path, comfyapi_client: comfy.ComfyAPI):
        wf= cls(path, comfyapi_client)

        wf.links: Dict[int, Link]= {}
        for l in wf._data.get("links", []):
            link= Link(*l)
            logger.info(link)
            wf.links[link.id]= link
            
        wf.nodes: Dict[int, Node]= {}
        for n in wf._data.get("nodes", []):
            node= await Node.create(n, wf.links, wf.comfyapi_client)
            wf.nodes[node.id]= node
            logger.info(f"Node {node.id} disabled {node.disabled}")
        wf.prune_source_links()
        return wf
    
    async def save_api(self, path: pathlib.Path):
        data= await self.generate_api_workflow()
        try:
            with open(path, 'w', encoding='utf-8') as file:
                self._data= json.dump(data, file, indent=2)
        except Exception as e:
            logger.critical("Error loading json file %s : %s", path.name, e)
            raise e
        
    def prune_source_links(self):
        # Set final src and dest skipping disabled nodes
        for link in self.links.values():
            src_node: Node= self.nodes[link.src_node]
            while src_node.disabled:
                follow_link= self.find_link_by_dst(link.final_src_node, link.final_src_pos)
                if follow_link is None:
                    logger.error("Unable to follow disabled link")
                    break
                link.final_src_node= follow_link.src_node
                link.final_src_pos= follow_link.src_pos
                src_node= self.nodes[link.final_src_node]

            if link.final_src_node != link.src_node:
                logger.info(f"Link {link.id} changed source {link.src_node}:{link.src_pos} to {link.final_src_node}:{link.final_src_pos}")        

    def find_link_by_dst(self, node: int, pos: int) -> Optional[Link]:
        for link in self.links.values():
            if link.dst_node == node and link.dst_pos == pos:
                return link
        return None

    def find_link_by_src(self, node: int, pos: int) -> Optional[Link]:
        for link in self.links.values():
            if link.src_node == node and link.src_pos == pos:
                return link
        return None

    async def generate_api_workflow(self) -> Optional[dotdict]:
        api: dotdict= dotdict()
        node: Node
        ids= list(self.nodes.keys())
        ids.sort()
        for node_id in ids:
#        for node in self.nodes.values():
            node= self.nodes[node_id]
            if node.disabled:
                continue
            add_node: dotdict= dotdict()
            add_node.inputs= dotdict()
            add_node.class_type= node.class_type            
            input: NodeInput
            for input in node.inputs:
                if input.input_type == NodeInputType.HIDDEN_WIDGET:
                    continue
                if input.input_type == NodeInputType.LINK:
                    link: Link= input.value
                    if link is None:
                        continue
                    value= [str(link.final_src_node), link.final_src_pos]
                else:
                    value= input.value
                add_node.inputs[input.input_name]= value
            api[str(node.id)]= add_node
        return api

# async def workflow_to_prompt(workflow: dict) -> dict:
#     '''
#     Reference web/scripts/app.js function graphToPrompt
#     Take as input a workflow (not an API workflow) and
#     output a prompt.
#     '''
#     prompt= {}
#     nodes= workflow.get("nodes", None)
#     if nodes is None:
#         logger.warning("No nodes in the worklow.")
#         return prompt
    
#     for node in nodes:
#         node_id= node.get("id", None)
#         if node_id is None:
#             logger.warning("Node without id, cannot be converted: %r", node)
#             continue
#         mode= node.get("mode", 0)
#         if mode in [2, 4]:
#             #Skip muted
#             continue        
        
#         widgets= node.get("widgets", None)
#         if widgets is None:
#             logger.warning("Node without widgets, cannot be converted: %r", node)
#             continue
        
#         for widget in widgets:
#             if widget.get("options", {}).get("serialize", False) == True:
#                 continue
            
#         inputs= {}

#         prompt_node= {"inputs":inputs}
        
#     pass