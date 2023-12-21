"""
A wrapper for the comfyUI's API to provide convenient event callbacks during workflow execution.

Known limitations:
- multiple output nodes untested, probably won't work
"""

import abc
import dataclasses
import io
import json
import logging
from typing import Any, Callable, Union, Optional, List
import uuid
import struct
from PIL import Image
from io import BytesIO
import aiohttp
import aiohttp.client_exceptions
from dot4dict import dotdict

logger= logging.getLogger("comfyui_utils").getChild("comfy")

# Inherit this class to specify callbacks during prompt execution.
class Callbacks(abc.ABC):
    @abc.abstractmethod
    async def queue_position(self, position: str):
        """Called when the prompt's queue position updates, with the position in the queue (0 = already being executed)."""
    @abc.abstractmethod
    async def in_progress(self, node_id: int, progress: int, total: int):
        """Called when a node is in progress, with current and total steps."""
    @abc.abstractmethod
    async def completed(self, outputs: dict[str, Any], cached: bool):
        """Called when the prompt completes, with the final output."""
    @abc.abstractmethod
    async def image_received(self, image: Image.Image):
        """Called when the prompt's queue return a sampler image, a pillow image object is pass to this function"""


StrDict = dict[str, Any]  # parsed JSON of an API-formatted ComfyUI workflow.


def _parse_queue(queue_json):
    """Returns a list of prompt IDs in the queue, the 0th (if present) element is currently executed."""
    assert len(queue_json["queue_running"]) <= 1
    result = []
    if queue_json["queue_running"]:
        result.append(queue_json["queue_running"][0][1])
    for pending in queue_json["queue_pending"]:
        result.append(pending[1])
    return result


def _find_prompt_in_history(history, prompt):
    for prompt_id, data in history.items():
        original_prompt = data["prompt"][2]
        if original_prompt == prompt:
            return prompt_id
    return None


@dataclasses.dataclass
class PromptSession:
    client_id: str
    prompt_id: str
    prompt: StrDict
    session: aiohttp.ClientSession
    address: str

async def _get_queue_position_or_cached_result(sess: PromptSession) -> Union[int, List[StrDict]]:
    """Returns """
    async with sess.session.get(f"http://{sess.address}/queue") as queue_resp:
        queue = await queue_resp.json()
        queue = _parse_queue(queue)
        logger.debug("Prompt id : %s QUEUE: %s", sess.prompt_id, queue)
        if sess.prompt_id in queue: # Prompt is queued.
            return queue.index(sess.prompt_id)
        # Prompt is cached, so not queued. Have to fetch output info from history.
        async with sess.session.get(f"http://{sess.address}/history") as history_resp:
            history = await history_resp.json()
            cached_id = _find_prompt_in_history(history, sess.prompt)
            if cached_id is None:
                raise ValueError("Response seems cached, but not found in history!")
            cached_outputs = history[cached_id]["outputs"]
            # Put the node inside the result dict for convenience and return a list of results
            results= []
            for node_id, node_result in cached_outputs.items():
                result= node_result
                result['node']= node_id
                #Some custom nodes output texts as an array of single characters, fix the output here.
                text_result= result.get('text', None)
                if issubclass(type(text_result), list):
                    for c in text_result:
                        if len(c) != 1 or not issubclass(type(c), str):
                            break
                    else:
                        result['text']= [''.join(text_result)]
                results.append(result)
            return results



async def _prompt_websocket(sess: PromptSession, callbacks: Callbacks) -> None:
    """Connects to a websocket on the given address/session and invokes callbacks to handle prompt execution."""
    async with sess.session.ws_connect(f"ws://{sess.address}/ws?clientId={sess.client_id}") as ws:
        current_node = None
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.ERROR:
                logger.debug("WS Error message received : %r", msg.data)
                raise BrokenPipeError(f"WebSocket error: {msg.data}")
            if msg.type == aiohttp.WSMsgType.TEXT:
                message = json.loads(msg.data)
                logger.debug("Text message received\n%s", msg.data)
                # Handle prompt being started.
                if message["type"] == "status":
                    queue_or_result = await _get_queue_position_or_cached_result(sess)
                    if isinstance(queue_or_result, int):
                        await callbacks.queue_position(queue_or_result)
                    else:
                        await callbacks.completed(queue_or_result, True)
                        break
                # Handle a node being executed.
                if message["type"] == "executing":
                    if message["data"]["node"] is not None:
                        node_id = int(message["data"]["node"])
                        current_node = node_id
                        await callbacks.in_progress(current_node, 0, 0)
                # Handle completion of the request.
                if message["type"] == "executed":
                    #Sometime the prompt id is different if the result come from cache
                    #assert message["data"]["prompt_id"] == sess.prompt_id
                    #Check if still running. Can have multiple executed
                    queue_or_result = await _get_queue_position_or_cached_result(sess)
                    if not isinstance(queue_or_result, int):
                        await callbacks.completed(queue_or_result, False)
                        break
                # Handle progress on a node.
                if message["type"] == "progress":
                    progress = int(message["data"]["value"])
                    total = int(message["data"]["max"])
                    await callbacks.in_progress(current_node, progress, total)
            elif msg.type == aiohttp.WSMsgType.BINARY:
                image= await receive_image(msg.data)
                if image is not None:
                    await callbacks.image_received(image)
            else:
                logger.warning("Not text message, message type: %r", msg.type)            


async def receive_image(image_data) -> Optional[Image.Image]:
    '''
    Reference : https://github.com/comfyanonymous/ComfyUI.git server.py function send_image
    Rebuild an PIL Image from the data received on the websocket. Return None on any errors.
    '''
    try:
        type_num, = struct.unpack_from('>I', image_data, 0)
        event_type_num, = struct.unpack_from('>I', image_data, 4)
        if type_num == 1:
            image_type= "JPEG"
        elif type_num == 2:
            image_type= "PNG"
        else:
            logger.error("Unsuported type received : %d", type_num)
            return None
        
        if event_type_num == 1:
            event_type= "PREVIEW_IMAGE"
        elif event_type_num == 2:
            event_type= "UNENCODED_PREVIEW_IMAGE"
        else:
            event_type= f"UNKNOWN {event_type_num}"
        logger.info(f"Received an {image_type} ({event_type})")
        bytesIO = BytesIO(image_data[8:])
        image= Image.open(bytesIO)
        return image        
    except Exception as e:
        logger.exception("Error on receiving image.")
    return None

class ComfyAPI:
    def __init__(self, address):
        self.address = address
        self.client_id = str(uuid.uuid4())

    async def get_all_object_info(self) -> Optional[dotdict]:
        """Fetch object info from ComfyUI

        Args:
            class_type (str): Object class type to retrieve

        Returns:
            Optional[dict]: A dotdict (dict accessible by dot notation via json.loads) of the object if found, none otherwise
        """
        async with aiohttp.ClientSession() as session:            
            async with session.get(f"http://{self.address}/object_info") as object_resp:
                json_resp= await object_resp.json()
                obj= dotdict(json_resp)
                return obj

    async def get_object_info(self, class_type: str) -> Optional[dotdict]:
        """Fetch object info from ComfyUI

        Args:
            class_type (str): Object class type to retrieve

        Returns:
            Optional[dict]: A dotdict (dict accessible by dot notation via json.loads) of the object if found, none otherwise
        """
        async with aiohttp.ClientSession() as session:            
            async with session.get(f"http://{self.address}/object_info/{class_type}") as object_resp:
                json_resp= await object_resp.json()
                obj= dotdict(json_resp)
                return obj.get(class_type, None)


    async def fetch(self, filename: str, callback: Callable[[io.BytesIO], None], *args, **kwargs):
        """Fetch a generated piece of data from Comfy.
            Invokes callback with an io.BytesIO object."""
        async with aiohttp.ClientSession() as session:
            async with session.get(f"http://{self.address}/view", params=filename) as resp:
                data = await resp.read()
                with io.BytesIO(data) as data_file:
                    await callback(data_file, *args, **kwargs)

    async def submit(self, prompt: StrDict, callbacks: Callbacks, extra_data: Optional[dict]= None):
        if extra_data is not None:
            init_data = json.dumps({
                "prompt": prompt,
                "client_id": self.client_id,
                "extra_data": extra_data
            }).encode('utf-8')
        else:            
            init_data = json.dumps({
                "prompt": prompt,
                "client_id": self.client_id
            }).encode('utf-8')
        async with aiohttp.ClientSession() as session:
            # Enqueue and get prompt ID.
            async with session.post(f"http://{self.address}/prompt", data=init_data) as resp:
                try:
                    response_json = await resp.json()
                    logger.info(response_json)
                    if "error" in response_json:
                        if "node_errors" not in response_json:
                            raise ValueError(response_json["error"]["message"])
                        errors = []
                        for node_id, data in response_json["node_errors"].items():
                            for node_error in data["errors"]:
                                errors.append(f"Node {node_id}, {node_error['details']}: {node_error['message']}")
                        raise ValueError("\n" + "\n".join(errors))

                    prompt_id = response_json['prompt_id']
                    logger.debug("Prompt ID: %s", prompt_id)
                except aiohttp.client_exceptions.ContentTypeError as e:
                    text= await resp.text()
                    logger.error("Error, unexpected response, not a json response. %s \nReceived : \n%s", e, text)
                    raise ValueError("Not a JSON response : \n%s" % text)
                    
            # Listen on a websocket until the prompt completes and invoke callbacks.
            await _prompt_websocket(PromptSession(
                client_id=self.client_id,
                prompt_id=prompt_id,
                prompt=prompt,
                session=session,
                address=self.address
            ), callbacks)
