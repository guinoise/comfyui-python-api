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