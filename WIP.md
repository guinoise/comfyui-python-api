## Multiple outputs

#comfy.py
**Breaking change**
Function _get_queue_position_or_cached_result now return Union[int, List[StrDict]] instead of Union[int, StrDict]

Instead of returning the first output from the cached_outputs, loop in the
results, move the key (node id) into the attribute node and append to the list.

In ComfyAPI, change the fetch definition to pass *args and **kargs to the callback function.

** Todo **
Fix the e2e I scrapped it