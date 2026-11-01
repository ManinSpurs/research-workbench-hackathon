"""A local editing fallback, not a pretend local language model."""
from .adapters import Usage
from .results import RefinedQuestion
class OfflineAdapter:
    label='offline'
    async def refine(self,question):return RefinedQuestion(refined_text=question,kept_constraints=[]),Usage(0,0,0)
    async def first_step(self,*args):
        from .harness import Conflict
        raise Conflict('No local inference model configured. Canvas, data profile and saved work remain available.')
    plan=first_step
    assess=first_step
    basic=first_step
