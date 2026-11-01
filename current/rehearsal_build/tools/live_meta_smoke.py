"""Explicit, synthetic-only live smoke. Prepare does not send research context or enable cloud."""
import argparse,asyncio,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from app.harness import Harness
from app.offline import OfflineAdapter
from app.ai.service import AIService
from app.ai.provider import ProviderError

async def main():
    parser=argparse.ArgumentParser();parser.add_argument('--execute-preview-hash');args=parser.parse_args()
    output=ROOT.parent/'logs';output.mkdir(exist_ok=True)
    h=Harness(str(output/'synthetic_live_qa.db'),adapter=OfflineAdapter());svc=AIService(h)
    try:
        if args.execute_preview_hash:
            preview=json.loads((output/'LIVE_SMOKE_PREVIEW.json').read_text())
            if preview['preview_hash']!=args.execute_preview_hash:raise SystemExit('Preview hash mismatch')
            svc.configure(True,5)
            try:
                result=await svc.execute(preview['job_id'],preview['preview_hash'],True)
                report={'status':'EXECUTED','source':'LIVE_META_API','result':result}
            except Exception as exc:
                report={'status':'STOPPED','source':'LIVE_META_API','error_type':type(exc).__name__,
                        'jobs':svc.jobs(preview['transmission']['text_and_context']['pack']['question_id'])}
            finally:svc.configure(False,5)
            (output/'LIVE_SMOKE_RESULT.json').write_text(json.dumps(report,indent=2));print(json.dumps(report,indent=2))
        else:
            p=h.register_project('fx')['project_id'];q=await h.create_question(p,'What does est_cost measure?')
            await svc.verify_account()
            preview=svc.prepare(q['question_id'],{'task':'research_query','model':'muse-spark-1.3','compare':True,'max_output_tokens':2048,'max_cost_usd':5.0})
            (output/'LIVE_SMOKE_PREVIEW.json').write_text(json.dumps(preview,indent=2))
            print(json.dumps({'job_id':preview['job_id'],'preview_hash':preview['preview_hash'],'model':preview['model'],
                  'max_inference_calls':preview['max_inference_calls'],'maximum_reserved_usd':preview['maximum_reserved_usd'],
                  'files':preview['transmission']['files'],'whole_csv_rows':False,'source':'synthetic FX fixture dictionaries only','preview_file':str(output/'LIVE_SMOKE_PREVIEW.json')},indent=2))
    finally:h.db.close()
if __name__=='__main__':asyncio.run(main())
