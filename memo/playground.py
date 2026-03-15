from pydantic import AfterValidator, BaseModel, model_validator
from typing import Any, Annotated
import textwrap
import inspect


# -------------------------------------------------------------
# on field itself only first validator raises, all are executed
# -------------------------------------------------------------

def scalar_validator_1(value: Any) -> Any:
    print('scalar_validator_1')
    if value == 'error':
        raise ValueError('error_1')
    return value

def scalar_validator_2(value: Any) -> Any:
    print('scalar_validator_2')
    if value == 'error':
        raise ValueError('error_2')
    return value

Scalar_1 = Annotated[str, AfterValidator(scalar_validator_1), AfterValidator(scalar_validator_2)]

class Composite_1(BaseModel):
    f_1: Scalar_1

# x = Composite_1(f_1='222') 
# x = Composite_1(f_1='error') 



# ---------------------------
# predicate as AfterValidator
# ---------------------------

class BaxModel(BaseModel):
    
    @staticmethod
    def with_predicate(errmsg: str):
        def decorated(predicate: Any):
            def pydantic_after_validator(obj: Any):
                if not predicate(obj):
                    raise ValueError(errmsg)
                return obj
            return pydantic_after_validator
        return decorated

    @classmethod
    def predicates(cls):
        qual_name = 'BaxModel.with_predicate.<locals>.decorated.<locals>.pydantic_after_validator'
        return [
            (name, func) 
            for name, func in inspect.getmembers_static(Composite_2, inspect.isfunction) 
            if func.__qualname__ == qual_name
        ]



class Composite_2(BaxModel):
    f_1: Scalar_1

    @model_validator(mode='after')
    @BaxModel.with_predicate(errmsg='error error')
    def model_predicate_1(self) -> bool:
        """f_1 neq error_2"""
        return self.f_1 != 'error_2'   

print(Composite_2.predicates())
for __, func in Composite_2.predicates():
    print(textwrap.dedent(inspect.getsource(func)))

# print(Composite_2.model_predicate_1.__qualname__)
# print([(name, func) 
#        for name, func in inspect.getmembers_static(Composite_2, inspect.isfunction) 
#        if func.__qualname__ == 'BaxModel.with_predicate.<locals>.decorated.<locals>.pydantic_after_validator'])





# print(isfunction(Composite_2.model_predicate_1))
# x = Composite_2(f_1='222') 
# print(Composite_2.model_predicate_1.__closure__)
# x = Composite_2(f_1='error_2') 
