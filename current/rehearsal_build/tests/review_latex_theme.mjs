// Computed writing contrast in both explicit themes, with no app/server/network.
import {chromium} from 'playwright';
import {readFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
const root=new URL('../frontend/',import.meta.url);
const [base,writing,html]=await Promise.all(['style.css','writing.css','index.html'].map(f=>readFile(new URL(f,root),'utf8')));
const otherStyles=await Promise.all(['startup.css','desktop_shell.css','home_hub.css','console_motion.css'].map(f=>readFile(new URL(f,root),'utf8')));
const documentHtml=html.replace(/<script\b[^>]*>[\s\S]*?<\/script>/g,'').replace(/<link\b[^>]*>/g,'');
const browser=await chromium.launch();
try {
  const page=await browser.newPage({viewport:{width:1320,height:860}});
  await page.route('**/*',route=>route.abort());
  await page.setContent(documentHtml);
  await page.addStyleTag({content:[base,...otherStyles,writing].join('\n')});
  await page.evaluate(()=>{
    document.documentElement.classList.add('desktop-shell');
    document.body.classList.add('workbench-open');
    document.querySelector('#startup').remove();
    const portal=document.querySelector('#portal');
    portal.hidden=false; portal.classList.add('expanded'); portal.style.inset='0';
    document.querySelector('#app-shell').classList.remove('hidden');
    document.querySelector('main section').classList.add('writing-mode');
    document.querySelector('#latex-view').classList.remove('hidden');
    document.querySelector('#latex-draft-select').innerHTML='<option>Untitled paper</option>';
    document.querySelector('#latex-files').innerHTML='<button>main.tex · main</button><button>references.bib</button>';
    document.querySelector('#latex-absence').hidden=false;
    document.querySelector('#latex-absence').textContent='PDF preview needs a LaTeX engine. You can still write and autosave.';
  });
  for (const moreOpen of [false,true]) {
    await page.locator('.writing-syntax > details').evaluate((el,open)=>{el.open=open;},moreOpen);
    const layout=await page.evaluate(()=>{
      const box=id=>document.querySelector(id).getBoundingClientRect();
      return {editor:box('#latex-editor').height,workspaceBottom:box('#latex-view').bottom,
        toolbarTop:box('.writing-toolbar').top,toolbarHeight:box('.writing-toolbar').height,viewport:innerHeight,
        centerWidth:box('#latex-view').width,
        filesOverflow:document.querySelector('.writing-files').scrollWidth-document.querySelector('.writing-files').clientWidth};
    });
    assert.ok(layout.editor>=200,`editor stays usable with More syntax open or closed: ${JSON.stringify(layout)}`);
    assert.ok(layout.toolbarHeight<=120,`toolbar stays within two rows: ${JSON.stringify(layout)}`);
    assert.equal(layout.filesOverflow,0,'files have no horizontal overflow');
    assert.ok(layout.workspaceBottom<=layout.viewport,'workspace stays within desktop viewport');
    assert.ok(layout.toolbarTop>=0,'paper toolbar stays visible');
    await page.locator('#latex-editor').focus();
    assert.equal(await page.evaluate(()=>scrollY),0,'editor focus does not scroll document');
  }
  for(const theme of ['light','dark']) {
    await page.evaluate(theme=>{
      document.documentElement.dataset.theme=theme;
      document.querySelector('#latex-editor').value='Visible source';
      document.querySelector('#latex-draft-select').innerHTML='<option>Paper</option>';
      document.querySelector('#latex-file-error').textContent='Invalid path';
      document.querySelector('#latex-status').dataset.state='dirty';
      document.querySelector('#latex-file-dialog').showModal();
    },theme);
    const results=await page.evaluate(()=>{
      const rgb=color=>color.match(/[\d.]+/g).slice(0,3).map(Number);
      const luminance=color=>rgb(color).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((a,v,i)=>a+v*[.2126,.7152,.0722][i],0);
      const selectors=['#latex-notice','#latex-citation-note','#latex-absence','#latex-editor','#latex-draft-select',
        '#latex-save','#latex-file-dialog','#latex-file-path','#latex-file-path-help','#latex-file-error','#latex-file-confirm','#latex-status'];
      return selectors.map(selector=>{
        const el=document.querySelector(selector),style=getComputedStyle(el);
        let parent=el,background=style.backgroundColor;
        while(background==='rgba(0, 0, 0, 0)'&&parent.parentElement) {
          parent=parent.parentElement;background=getComputedStyle(parent).backgroundColor;
        }
        const a=luminance(style.color),b=luminance(background);
        return {selector,ratio:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};
      });
    });
    for(const result of results) assert.ok(result.ratio>=4.5,`${theme} ${result.selector}: ${result.ratio.toFixed(2)} contrast`);
    await page.locator('#latex-file-path').focus();
    const focus=await page.locator('#latex-file-path').evaluate(el=>{
      const style=getComputedStyle(el);
      return {outline:style.outlineColor,accent:getComputedStyle(document.documentElement).getPropertyValue('--accent').trim(),width:style.outlineWidth};
    });
    assert.equal(focus.width,'2px');
    assert.ok(focus.outline!=='rgba(0, 0, 0, 0)');
    await page.locator('#latex-file-dialog').evaluate(el=>el.close());
  }
  await page.setViewportSize({width:390,height:860});
  await page.locator('#latex-save').scrollIntoViewIfNeeded();
  assert.equal(await page.locator('#latex-save').isVisible(),true);
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth),'narrow stack has no horizontal clipping');
  console.log('PASS computed writing/editor/select/dialog/error/muted contrast >=4.5 in Light and Dark; focus visible');
} finally {
  await browser.close();
}
