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

logger= logging.getLogger("comfyui_utils").getChild("workflow2prompt")

_cached_object_infos: Dict[str, Dict]= {}

async def _get_object_infos(class_type: str, comfyapi_client: comfy.ComfyAPI) -> Optional[dict]:
    if class_type in _cached_object_infos.keys():
        logger.debug("Object info %s served from local cache", class_type)
        return _cached_object_infos[class_type]
    logger.info("Retrieve object info on class type %s from ComfyUI", class_type)
    infos= await comfyapi_client.get_object_info(class_type=class_type)
    if infos is not None:
        logger.debug("Object info %s retrieved from ComfyUI", class_type)
        _cached_object_infos[class_type]= infos
    else:
        logger.warning("Unable to retrieve object info %s from ComfyUI", class_type)
    return infos

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

@dataclass
class Widget:
    input_name: str
    widget_name: str
    value_index: int
    value: Any

class Node:
    id: int
    inputs: Dict[str,Any]
    class_type: str
    disabled: bool
    mode: int
    outputs: Dict[str, Any]
    widgets_values: List[Any]
    widgets: List[Widget]
    object_infos: Optional[Dict]
    
    @classmethod
    async def create(cls, json_input: dict, links: Dict[int, Link], comfyapi_client: comfy.ComfyAPI):
        node= cls()
        if "id" not in json_input.keys():
            raise ValueError("Invalid json_input, id is missing")
        node.class_type= json_input.get("type")
        node.inputs= None
        node.outputs= None
        node.widgets= []
        node.widgets_values= None
        node.object_infos= await _get_object_infos(node.class_type, comfyapi_client)
        obj_inputs: List[dotdict]= []
        if node.object_infos is not None:
            for k,v in node.object_infos["input"].items():
                obj_inputs.update(v)
        for k, v in json_input.items():
            if k == "type":
                continue
            setattr(node, k, v)
        node.disabled= node.mode in [2,4]

        if node.inputs is not None:
            widget_index=0 
            for input in node.inputs:
                input_def= obj_inputs.pop()
                if input.get("link", None) is not None:
                    input["link"]= links[input["link"]]
                if input.get("widget", None) is not None:
                    widget= Widget(
                        input_name=input["name"],
                        widget_name=input["widget"]["name"],
                        value_index= widget_index,
                        value= node.widgets_values[widget_index]
                    )
                    node.widgets.append(widget)
                    widget_index+= 1

        if node.outputs is not None:
            for output in node.outputs:
                if output.get("links", None) is not None:
                    new_links= []
                    for link_id in output["links"]:
                        new_links.append(links[link_id])
                    output["links"]= new_links
        return node            

class Workflow:
    original_json: Dict
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
            with open(path, 'r') as file:
                self.original_json= json.load(file)
        except Exception as e:
            logger.critical("Error loading json file %s : %s", path.name, e)
            raise e
        
    @classmethod
    async def load(cls, path: pathlib.Path, comfyapi_client: comfy.ComfyAPI):
        wf= cls(path, comfyapi_client)
        wf.comfyapi_client= comfyapi_client
        if not path.is_file():
            raise ValueError(f"File {path.resolve()} does not exists")
        wf.filepath= path
        
        try:
            with open(path, 'r') as file:
                wf.original_json= json.load(file)
        except Exception as e:
            logger.critical("Error loading json file %s : %s", path.name, e)
            raise e
        
        wf.links: Dict[int, Link]= {}
        for l in wf.original_json.get("links", []):
            link= Link(*l)
            logger.info(link)
            wf.links[link.id]= link
            
        wf.nodes: Dict[int, Node]= {}
        for n in wf.original_json.get("nodes", []):
            node= await Node.create(n, wf.links, wf.comfyapi_client)
            wf.nodes[node.id]= node
            logger.info(f"Node {node.id} disabled {node.disabled}")
        wf.prune_source_links()
        return wf
        
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