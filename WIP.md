## Multiple outputs

#comfy.py
**Breaking change**
Function _get_queue_position_or_cached_result now return Union[int, List[StrDict]] instead of Union[int, StrDict]

Instead of returning the first output from the cached_outputs, loop in the
results, move the key (node id) into the attribute node and append to the list.

In ComfyAPI, change the fetch definition to pass *args and **kargs to the callback function.

** Todo **
Fix the e2e I scrap## Workflow conversion to api workflow

## Workflow conversion to api workflow

Notes:
- Using dotdict for more easy access
- Added examples/out folder in the gitignore. 
- comfy :
    - Added support get_all_object_info
    - Added support get_object_info
    - Added the option of extra_data in the submit call (optional parameter)
- Added class workflow2prompt 

Limitations
- Added class workflow2prompt 
    - seed/noise seed manual fix (seems the same everywhere)
    - manual fix on the SDParameterGenerator node to avoid including the control widget after the seed (seems to be an exception)
    - Cannot load object infos one at the time for //INSPIRE pack (the slashes seems to be an issue)
- Issues encoding...

Still to do:
- Optimisation
- Test classes
- e2e_workflow.py not completed
- Optimize/review : Added the option of extra_data in the submit call (optional parameter)
- Added class workflow2prompt 
    - Class name / package to review
- split folder workflow into workflow_ui and workflow_api