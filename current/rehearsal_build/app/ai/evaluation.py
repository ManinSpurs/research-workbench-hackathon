"""Human-scored, denominator-explicit evaluation of recorded outputs. Never calls a provider."""
import json,math
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
class ArmReview(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    claim_verdicts:list[Literal['supported','contradicted','unverifiable']]
    citation_verdicts:list[Literal['supports','wrong','unknown']]
    variable_method_correct:bool|None
    task_success:bool|None
class Review(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    reviewer:str=Field(min_length=1,max_length=120)
    gold_case_id:str=Field(min_length=1,max_length=120)
    rubric_version:Literal['rw-research-1']
    arms:dict[str,ArmReview]

def evaluate(result,review):
    review=Review.model_validate(review)
    actual={k:v for k,v in result['outputs'].items() if k in ('basic','harness')}
    if set(actual)!=set(review.arms):raise ValueError('Review must cover the exact recorded arms')
    scores={}
    for name,arm in actual.items():
        r=review.arms[name];claims=arm['result']['claims'];citations=sum(len(c['evidence_ids']) for c in claims)
        if len(r.claim_verdicts)!=len(claims) or len(r.citation_verdicts)!=citations:raise ValueError('One verdict is required for every claim and citation occurrence')
        scores[name]={'evidence_accuracy':r.claim_verdicts.count('supported')/len(claims) if claims else None,
            'false_citation_rate':r.citation_verdicts.count('wrong')/citations if citations and 'unknown' not in r.citation_verdicts else None,
            'researcher_verifiable_fraction':sum(c['kind']=='fact' and bool(c['evidence_ids']) and v=='supported' for c,v in zip(claims,r.claim_verdicts))/len(claims) if claims else None,
            'variable_method_accuracy':int(r.variable_method_correct) if r.variable_method_correct is not None else None,
            'task_success':r.task_success,'claim_count':len(claims),'citation_count':citations,
            'structural_citation_error_rate':arm['grounding']['citation_error_rate']}
    scores['usage']={'elapsed_seconds':result['elapsed_seconds'],'calculated_cost_usd':result['calculated_cost_usd'],
        'stages':result['stages'],'note':'Paired total includes planning; stage timings and token usage permit per-arm accounting.'}
    return {'rubric_version':review.rubric_version,'reviewer':review.reviewer,'gold_case_id':review.gold_case_id,'scores':scores,'review':review.model_dump(),'improvement_claim':'NOT_COMPUTED'}
