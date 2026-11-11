/* ЛК студента: заказы справок, выписок и заявлений. Данные — через API сервиса (а он — из 1С). */
(function(){
"use strict";
const $=s=>document.querySelector(s);
const esc=s=>String(s??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const LS={get(k){try{return JSON.parse(localStorage.getItem(k))}catch(e){return null}},set(k,v){try{localStorage.setItem(k,JSON.stringify(v))}catch(e){}}};
const fmt=s=>s?String(s).slice(8,10)+"."+String(s).slice(5,7)+"."+String(s).slice(0,4):"";
const MONTHS=["январь","февраль","март","апрель","май","июнь","июль","август","сентябрь","октябрь","ноябрь","декабрь"];
const monthName=k=>{if(!k)return "";const [y,m]=k.split("-");return MONTHS[+m-1]+" "+y};
const plural=(n,a,b,c)=>{const x=Math.abs(n)%100,y=x%10;return x>10&&x<20?c:y>1&&y<5?b:y===1?a:c};
/* даты в заявлениях — те же правила, что на сервере (lk/dates.py) */
const isoD=d=>`${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
const pD=s=>/^\d{4}-\d\d-\d\d$/.test(s||"")?new Date(+s.slice(0,4),+s.slice(5,7)-1,+s.slice(8,10)):null;
function addD(d,y=0,m=0,days=0){const mm=d.getMonth()+m+12*y,Y=d.getFullYear()+Math.floor(mm/12),M=((mm%12)+12)%12,last=new Date(Y,M+1,0).getDate();
  const r=new Date(Y,M,Math.min(d.getDate(),last));r.setDate(r.getDate()+days);return r}
function earliest(rule){if(!rule)return null;const t=new Date();t.setHours(0,0,0,0);
  if(rule.after_today)return addD(t,0,0,1);if(rule.today)return t;const n=+rule.workdays||0;if(!n)return null;const H=new Set(ME.holidays||[]);let d=new Date(t),k=0;
  while(k<n){d=addD(d,0,0,1);if(d.getDay()%6!==0&&!H.has(isoD(d)))k++}return d}
function calcVal(c,v){if(c.join)return c.join.map(k=>String(v[k]||"").trim()).filter(Boolean).join(" ");const b=pD(v[c.from]);if(!b)return "";let y=c.years||0,m=c.months||0,d=c.days||0;
  if(c.by){const s=(c.map||{})[v[c.by]];if(!s)return "";y+=s.years||0;m+=s.months||0;d+=s.days||0}
  if(c.days_field){const x=parseInt(v[c.days_field],10);if(!(x>0))return "";d+=x}return isoD(addD(b,y,m,d))}
function minMsg(f,low){return f.min.today?"Не раньше даты подачи заявления":f.min.after_today?`Любой день, кроме дня подачи заявления: не раньше ${fmt(isoD(low))}`
  :workdaysMsg(f.min.workdays)}
const nminOf=(f,v)=>{let x=f.nmin;if(x&&typeof x==="object")x=(x.map||{})[v[x.by]];return x==null?null:+x};
/* минимум числа зависит от другого поля (срок беременности: одноплодная — от 30 недель, многоплодная — от 28) */
const nByOff=(f,v)=>!!(f.nmin&&typeof f.nmin==="object"&&!v[f.nmin.by]);
const nPh=(f,v,fl)=>nByOff(f,v)?"Сначала выберите: "+(((fl||[]).find(x=>x.k===f.nmin.by)||{}).l||"").toLowerCase():nminOf(f,v)!=null?`не менее ${nminOf(f,v)}${f.unit?" "+f.unit:""}`:f.ph||"";
const otherL=f=>/причин/.test(f.other)?"Укажите, какие именно причины":"Укажите, какие именно обстоятельства";
const shownF=(f,v)=>!f.show||String(v[f.show.by]||"").includes(f.show.has||"");
const workdaysMsg=n=>`Срок начала отпуска устанавливается не ранее чем через ${({5:"пять",3:"три",10:"десять"})[n]||n} рабочих дней с момента подачи заявления, исключая день подачи, в соответствии со сроком рассмотрения заявления по Положению`;
/* выпадашки перевода: институты → направления института → профили; только по уровню текущей программы (СПО не переводится) */
const DIR_GROUP={bak:"bs",spec:"bs",mag:"mag",asp:"asp"},OTHER="_иное";
const dirLabel=d=>`${d.code} ${d.name}`;
function srcOpts(f,vals){const L=((ME.directions||{})[DIR_GROUP[prog().level]])||[],dep=vals[f.dep]||"";
  if(f.src==="inst")return [...new Set(L.map(d=>d.inst))].sort((a,b)=>a.localeCompare(b,"ru"));
  if(f.src==="dir")return L.filter(d=>!f.dep||d.inst===dep).map(dirLabel);
  if(f.src==="prof"){const d=L.find(d=>dirLabel(d)===dep);return d?d.profiles.map(x=>x.name):[]}
  if(f.src==="form"){const d=L.find(d=>dirLabel(d)===(vals[f.dirk]||"")),pr=d&&d.profiles.find(x=>x.name===dep),c=vals[f.course]||"1";
    /* КЦП — нынешний 1-й курс; на 2-й и старше показываем все формы (группы набраны в прошлые годы) */
    return [["очная","очной"],["очно-заочная","очно-заочной"],["заочная","заочной"]].filter(x=>c!=="1"||pr&&pr.forms.includes(x[0])).map(x=>x[1])}
  return f.opts||[]}
function toast(t){const e=$("#toast");e.textContent=t;e.hidden=false;clearTimeout(toast.t);toast.t=setTimeout(()=>e.hidden=true,3600)}

/* встраивание в портал (1С:Университет): адреса API — от раздела портала, вход и шапка — портальные, CSRF — из <meta> */
const EMBED=true,BASE="";
const csrf=()=>{const m=document.querySelector('meta[name="csrf-token"]');return m?m.content:""};
async function api(path,{method="GET",json,form}={}){
  const o={method,headers:{"X-LK":"1"},credentials:"same-origin"};if(method!=="GET"&&csrf())o.headers["X-CSRF-Token"]=csrf();
  if(json!==undefined){o.headers["Content-Type"]="application/json";o.body=JSON.stringify(json)}
  if(form)o.body=form;
  const r=await fetch(BASE+path,o);
  if(r.status===401&&path!=="api/login"){showLogin();const e=new Error("Войдите в личный кабинет");e.status=401;throw e}
  const d=(r.headers.get("Content-Type")||"").includes("json")?await r.json():null;
  if(!r.ok){const e=new Error((d&&d.error)||"Ошибка "+r.status);e.errors=d&&d.errors;e.status=r.status;throw e}
  return d}

/* ---------- вход ---------- */
function showLogin(){if(EMBED){document.body.innerHTML="<main style=\"max-width:720px;margin:4rem auto;padding:2rem;font-family:sans-serif\"><h1>Услуги МФЦ (Бета)</h1><p>Для входа откройте раздел из личного кабинета студента.</p></main>";return}$("#vApp").hidden=true;$("#vLogin").hidden=false;$("#lLogin").focus()}
if($("#loginForm"))$("#loginForm").addEventListener("submit",async e=>{e.preventDefault();const er=$("#lErr");er.hidden=true;
  try{const r=await api("api/login",{method:"POST",json:{login:$("#lLogin").value,password:$("#lPass").value}});
    if(r.role!=="student"){return}$("#lPass").value="";boot()}
  catch(x){er.textContent=x.message;er.hidden=false}});
/* в окне кабинета шапка скрыта (theme.js ставит класс embed): колокольчик уведомлений переезжает в строку вкладок */
if(document.documentElement.classList.contains("embed")&&$("#bell")&&document.querySelector(".tabs"))document.querySelector(".tabs").appendChild($("#bell"));
if($("#logout"))$("#logout").onclick=async()=>{await api("api/logout",{method:"POST"}).catch(()=>{});showLogin()};

/* ---------- состояние ---------- */
let ME=null,ORDERS=[],NTF=[],tab="orders",filter="cur",formState=null,q="";
const PRIK={};
const progs=()=>ME.programs;
const prog=()=>{const id=LS.get("lk_prog_"+ME.person.fio);return progs().find(p=>p.id===id)||progs()[0]};
/* у каждой программы (зачётной книжки) свой набор услуг (по уровню; у иностранных граждан — свой перечень) и свой подписант */
const services=()=>ME.services.filter(s=>(prog().services||[]).includes(s.id));
const svcOf=id=>ME.services.find(s=>s.id===id);
const signer=()=>prog().signer||{fio:"",pos:""};   /* подписант справки с ЭЦП (у аспирантуры ЭЦП нет) */
const MFC={email:"mfts@rgau-msha.ru",phone:"+74999771433",phoneT:"+7 (499) 977-14-33"};
const mfcContacts=()=>`<a href="mailto:${MFC.email}">${MFC.email}</a>, <a href="tel:${MFC.phone}" style="white-space:nowrap">${MFC.phoneT}</a>`;
const progLabel=x=>(x.book?"Зачётная книжка № "+x.book+" — ":"")+[`${x.code||""} ${x.name||""}`.trim(),x.form?x.form.replace(/ой$/,"ая")+" форма":"",x.course?x.course+" курс":""].filter(Boolean).join(", ");

async function boot(){
  try{ME=await api("api/me");if(ME.status)Object.assign(ST,ME.status);if(ME.flows)Object.assign(FLOW,ME.flows)}catch(e){if(e.status!==401){(document.getElementById("mfcApp")||document.body).innerHTML=`<p class="empty">${esc(e.message)}</p>`}return}
  if(ME.role!=="student"){return}
  await reload();render();if($("#vLogin"))$("#vLogin").hidden=true;$("#vApp").hidden=false;
  clearInterval(boot.t);boot.t=setInterval(async()=>{await reload();if(!formState&&$("#sheet").hidden)render();else renderTop()},60000)}
/* заказы и уведомления — только по выбранной программе: программы ведутся раздельно */
async function reload(){const pq="?program="+encodeURIComponent(prog().id);try{ORDERS=(await api("api/orders"+pq)).items;NTF=(await api("api/notices"+pq)).items}catch(e){}}

/* ---------- шапка ---------- */
function renderTop(){const p=prog();
  $("#meName").textContent=ME.person.fio;$("#meLevel").textContent=[ME.levels[p.level]||"",p.course?p.course+" курс":""].filter(Boolean).join(", ");
  $("#prog").innerHTML=progs().map(x=>`<option value="${esc(x.id)}"${x.id===p.id?" selected":""}>${esc(progLabel(x))}</option>`).join("");
  $("#progBox").hidden=progs().length<2;
  const unread=NTF.filter(n=>!n.read).length;$("#bellN").hidden=!unread;$("#bellN").textContent=unread;
  const need=ORDERS.filter(o=>o.status==="need").length;$("#needN").hidden=!need;$("#needN").textContent=need?"нужно действие: "+need:""}
$("#prog").onchange=async e=>{LS.set("lk_prog_"+ME.person.fio,e.target.value);formState=null;filter="cur";ORDERS=[];NTF=[];render();await reload();render()};
document.querySelectorAll(".tabs button").forEach(b=>b.onclick=()=>{tab=b.dataset.tab;formState=null;render()});

/* ---------- статусы ---------- */
/* названия статусов и маршруты приходят с сервера (lk/catalog.py: STATUS, FLOWS); здесь — запасные */
const ST={reg:["Зарегистрировано","Можно изменить или отменить"],work:["Передано исполнителю","Документ готовит исполнитель"],
  sign:["Передано на подпись","Подписывается и ставится печать"],need:["Требуется действие","Посмотрите комментарий МФЦ"],
  ready:["Выполнено","Можно забирать в Студенческом МФЦ, при себе паспорт или студенческий билет"],done:["Выдано","Документ получен"],
  signed:["Подписано","Готовый скан с ЭЦП — скачайте в карточке заявки"],rej:["Отклонено","Причина — в карточке заявки"],cancel:["Отменено",""]};
const FLOW={paper:["reg","sign","ready","done"],long:["reg","work","sign","ready","done"],app:["reg","work"],ecp:["sign","signed"]};
const isDirectorateApp=o=>!!o&&o.data&&!!o.data.app&&(o.flow||o.data.flow)==="app";
const isEcpOrder=o=>!!o&&o.data&&!!o.data.ecp&&(o.flow||o.data.flow)==="ecp";
const statusLabel=(o,s)=>isDirectorateApp(o)&&["work","sign"].includes(s)?"Передано в дирекцию":isEcpOrder(o)&&s==="sign"?"Передано на подпись":ST[s][0];
const statusHint=(o,s)=>isDirectorateApp(o)&&["work","sign"].includes(s)?"Заявление направлено в дирекцию института для дальнейшего рассмотрения.":isEcpOrder(o)&&s==="sign"?"Справка подписывается усиленной квалифицированной электронной подписью.":isEcpOrder(o)&&s==="signed"?"Справка и файл электронной подписи доступны для скачивания в карточке заявки.":ST[s][1];
const chip=(s,o=null)=>`<span class="st s-${s}">${esc(statusLabel(o,s))}</span>`;
const flowOf=o=>FLOW[o.flow||o.data.flow]||(o.data.ecp?FLOW.ecp:o.data.app?FLOW.app:FLOW.paper);
const stepsFor=o=>isDirectorateApp(o)&&["work","sign"].includes(o.status)?["reg",o.status]:flowOf(o);
const isFinal=o=>o.final??["done","signed","rej","cancel"].includes(o.status),canRev=o=>o.can_review??["done","signed"].includes(o.status);
const num=o=>o.onec_number||o.id;
function hint(o){if(o.sync==="queued"&&!o.onec_id&&o.status!=="cancel")return "Отправляется в МФЦ";
  if(o.status==="reg"&&o.data.app)return "Можно изменить или отозвать";
  if(isDirectorateApp(o)&&["work","sign"].includes(o.status)){
    const base="Заявление направлено в дирекцию института";
    if(o.to_archive!=null&&!o.archived)return `${base}. В архив через ${o.to_archive} ${plural(o.to_archive,"день","дня","дней")}`;
    return base+" для дальнейшего рассмотрения.";
  }
  if(o.status==="need"&&o.need_until)return `Исправьте до ${fmt(o.need_until)} включительно, иначе заявку отклонят автоматически`;
  if(o.to_archive!=null&&!o.archived)return (statusHint(o,o.status)?statusHint(o,o.status)+" ":"")+`В архив через ${o.to_archive} ${plural(o.to_archive,"день","дня","дней")}`;
  return statusHint(o,o.status)}

/* ---------- мои заказы ---------- */
const GROUP={cur:o=>!o.archived,act:o=>["reg","work","sign","need"].includes(o.status)&&!isFinal(o),need:o=>o.status==="need",ready:o=>["ready","signed"].includes(o.status),arch:o=>o.archived};
function renderOrders(){const L=ORDERS,c=k=>L.filter(GROUP[k]).length,rows=L.filter(GROUP[filter]);
  const F=[["cur","Актуальные"],["act","В работе"],["need","Нужно действие"],["ready","Готово"],["arch","Архив"]];
  const empty=filter==="cur"?(L.length?"Актуальных заявок нет. Завершённые — в архиве.":"Заявок пока нет. Нажмите зелёную кнопку «+ Подать заявку» — там справки, выписки и заявления.")
    :filter==="arch"?`Завершённые заявки попадают в архив через ${ME.keep_days} ${plural(ME.keep_days,"день","дня","дней")} после выдачи, отказа или отмены.`:"Таких заявок нет.";
  $("#vOrders").innerHTML=`<div class="chips">${F.map(([k,t])=>`<button class="chip" data-f="${k}" aria-pressed="${k===filter}">${t}<em>${c(k)}</em></button>`).join("")}</div>
  <div class="list"><div class="head"><span>Номер и дата</span><span>Документ</span><span>Статус</span><span>Что дальше</span></div>
  ${rows.length?rows.map(o=>{const p=progs().find(x=>x.id===o.program)||{};const meta=[];
    if(o.data.org)meta.push(esc(o.data.org.name));if(o.data.qty&&o.data.paper&&o.svc!=="call")meta.push(o.data.qty+" экз.");
    if(canRev(o))meta.push(o.review_at?`оценка ${o.rating} из 5`:`<span style="color:var(--amber)">оставьте отзыв</span>`);
    return `<button class="ord${o.status==="need"?" need":""}" data-o="${o.id}"><span class="ord-n">№ ${esc(num(o))}<br>от ${fmt(o.created)}</span>
      <span><span class="ord-t">${esc(o.title)}</span>${meta.length?`<span class="ord-m">${meta.map(m=>`<span>${m}</span>`).join("")}</span>`:""}</span>
      <span class="ord-s">${chip(o.status,o)}</span><span class="ord-h${o.status==="need"?" act":""}">${esc(hint(o))}</span></button>`}).join("")
    :`<div class="empty">${empty}</div>`}</div>`}
$("#vOrders").addEventListener("click",e=>{const f=e.target.closest("[data-f]");if(f){filter=f.dataset.f;render();return}
  const o=e.target.closest("[data-o]");if(o)openOrder(+o.dataset.o)});

/* ---------- карточка заказа ---------- */
function openSheet(html,wide){const b=$("#sheetBody");b.className="sheet"+(wide?" wide":"");b.innerHTML=html;$("#sheet").hidden=false;
  const f=b.querySelector("button,input,textarea,a");if(f)f.focus({preventScroll:true})}
function closeSheet(){$("#sheet").hidden=true;$("#sheetBody").dataset.o=""}
$("#sheet").addEventListener("click",e=>{if(e.target.id==="sheet"||e.target.closest("[data-x]"))closeSheet()});
document.addEventListener("keydown",e=>{if(e.key==="Escape"&&!$("#sheet").hidden)closeSheet()});
function labelOf(o,k){const f=((ME.forms[o.title]||{}).fields||[]).find(x=>x.k===k);return f?f.l:k}
function openOrder(id){const o=ORDERS.find(x=>x.id===id);if(!o)return;const p=progs().find(x=>x.id===o.program)||{},d=o.data,cur=o.status;
  const done=o.hist.map(h=>h.s),fl=["rej","cancel","need"].includes(cur)?[...new Set(done)]:stepsFor(o);
  const steps=fl.map(s=>{const h=o.hist.slice().reverse().find(x=>x.s===s),isCur=s===cur;
    const end=isCur&&isFinal(o);
    return `<div class="${h&&!isCur?"t-ok":end?"t-end"+(s==="rej"?" bad":""):isCur?"t-cur":"t-wait"}"><i></i><div><b>${esc(statusLabel(o,s))}</b><span>${h?fmt(h.at):"ожидается"}</span></div></div>`}).join("");
  const kv=[["Программа",esc(`${p.book?"зачётная книжка № "+p.book+", ":""}${p.code||""} ${p.name||""}, ${p.course||""} курс`)]];
  if(d.org)kv.push([o.svc==="call"?"Работодателю":"Для представления",`${esc(d.org.full)}<br><span class="note">ИНН ${esc(d.org.inn)}</span>`]);
  else if(d.place)kv.push(["Для представления",esc(d.place)]);
  if(o.svc==="call"&&d.reason)kv.push(["Основание",esc(d.reason)]);
  if(o.svc==="call")kv.push(["Период вызова",`${fmt(d.from)} — ${fmt(d.to)}`]);
  if(o.svc==="call"&&d.boss)kv.push(["Руководитель",esc(d.boss)]);
  if(["income","income2"].includes(o.svc))kv.push(["Период",`${monthName(d.from)} — ${monthName(d.to)}`]);
  if(d.ord)kv.push(["Приказ",`№ ${esc(d.ord.n)} от ${fmt(d.ord.date)}, «${esc(d.ord.title)}»`]);
  if(d.qty&&d.paper&&o.svc!=="call")kv.push(["Экземпляров",d.qty+(d.foreign&&o.svc==="extract"?" (один — в Управление международного сотрудничества)":"")]);
  if(d.paper)kv.push(["Получение","В Студенческом МФЦ, при себе паспорт или студенческий билет"]);
  if(d.signer)kv.push(["Подписант",esc(`${d.signer.pos}, ${d.signer.fio}`)]);
  if(d.fields)Object.entries(d.fields).forEach(([k,v])=>{if(v)kv.push([esc(labelOf(o,k)),/^\d{4}-\d\d-\d\d$/.test(v)?fmt(v):esc(v)])});
  if(d.files&&d.files.length)kv.push(["Файлы",d.files.map(f=>`<a href="${BASE}api/orders/${o.id}/files/${encodeURIComponent(f.stored)}" target="_blank" rel="noopener">${esc(f.name)}</a>`).join("<br>")]);
  const what=d.app?"заявление":"заявку",canRate=canRev(o);
  openSheet(`<div class="sheet-h"><div><h2>${esc(o.title)}</h2><span class="note">${d.app?"Заявление":"Заявка"} № ${esc(num(o))} от ${fmt(o.created)}</span></div><button class="x" data-x aria-label="Закрыть">×</button></div>
   <div class="row">${chip(cur,o)}${o.sync==="queued"&&!o.onec_id&&cur!=="cancel"?`<span class="note">Отправляется в МФЦ — это займёт несколько минут</span>`:""}</div>
   ${isDirectorateApp(o)&&["work","sign"].includes(cur)?`<div class="info">Заявление направлено в дирекцию института. Дальнейшее рассмотрение проходит там; дополнительных действий в личном кабинете не требуется.</div>`:""}
   ${isEcpOrder(o)&&cur==="sign"?`<div class="info">Справка подписывается усиленной квалифицированной электронной подписью.</div>`:""}
   ${o.comment&&["need","rej"].includes(cur)?`<div class="${cur==="need"?"warn":"info"}"><b>Комментарий МФЦ:</b> ${esc(o.comment)}${cur==="need"&&o.need_until?`<br><b>Срок — до ${fmt(o.need_until)} включительно.</b> Если не исправить, заявка будет отклонена автоматически (${ME.need_days} ${plural(ME.need_days,"рабочий день","рабочих дня","рабочих дней")} на исправление).`:""}</div>`:""}
   ${cur==="signed"?`<div class="ok">Справка подписана электронной подписью. Скачайте её и отправьте в организацию — распечатывать и заверять не нужно.</div>`:""}
   ${cur==="ready"?`<div class="ok">Документ готов. Заберите его в Студенческом МФЦ, при себе паспорт или студенческий билет.</div>`:""}
   <dl class="kv">${kv.map(([a,b])=>`<dt>${a}</dt><dd>${b}</dd>`).join("")}</dl>
   <div><div class="note" style="margin-bottom:6px">Ход выполнения</div><div class="tl">${steps}</div></div>
   ${canRate?`<div class="rev"><b>Отзыв о работе МФЦ</b><div class="stars" role="radiogroup" aria-label="Оценка">${[1,2,3,4,5].map(i=>`<button data-r="${i}" class="${(o._r||o.rating||0)>=i?"on":""}" role="radio" aria-checked="${(o._r||o.rating)===i}" aria-label="${i} из 5">★</button>`).join("")}<span class="note">${["Поставьте оценку","Очень плохо","Плохо","Нормально","Хорошо","Отлично"][o._r||o.rating||0]}</span></div>
     <textarea id="revTxt" class="inp" rows="3" maxlength="1000" placeholder="${(o._r||o.rating||5)<=3?"Расскажите, что пошло не так, — это поможет МФЦ":"Что понравилось, что можно улучшить (необязательно)"}">${esc(o._t??o.review??"")}</textarea>
     <div class="row"><button class="btn btn-sm btn-go" data-a="rev">${o.review_at?"Сохранить отзыв":"Отправить отзыв"}</button>${o.review_at?`<span class="note">Отзыв отправлен ${fmt(o.review_at)}</span>`:""}</div></div>`:""}
   <div class="row">${cur==="signed"||(cur==="done"&&d.ecp)?`<a class="btn btn-go" href="${BASE}api/orders/${o.id}/document">Скачать справку (PDF)</a><a class="btn" href="${BASE}api/orders/${o.id}/signature">Скачать подпись (.sig)</a>`:""}
     ${d.app?`<button class="btn" data-a="blank">Бланк заявления</button>`:o.svc==="call"?`<button class="btn" data-a="blank">Справка-вызов</button>`:""}
     ${cur==="need"?`<button class="btn btn-go" data-a="fix">Исправить данные</button>`:""}
     ${cur==="reg"?`<button class="btn" data-a="edit">Изменить</button><button class="btn btn-del" data-a="cancel">${d.app?"Отозвать заявление":"Отменить заявку"}</button>`:""}
     ${cur==="rej"?`<button class="btn" data-a="again">${d.app?"Подать заново":"Подать заново"}</button>`:""}</div>`);
  $("#sheetBody").dataset.o=id}
$("#sheetBody").addEventListener("click",async e=>{const id=+$("#sheetBody").dataset.o,o=ORDERS.find(x=>x.id===id);
  const n=e.target.closest("[data-n]");if(n){closeSheet();tab="orders";render();if(+n.dataset.n)openOrder(+n.dataset.n);return}
  if(!o)return;
  const r=e.target.closest("[data-r]");if(r){o._t=$("#revTxt").value;o._r=+r.dataset.r;openOrder(id);return}
  const b=e.target.closest("[data-a]");if(!b)return;const a=b.dataset.a;
  try{
    if(a==="rev"){const rating=o._r||o.rating;if(!rating){toast("Сначала поставьте оценку звёздами");return}
      const x=(await api(`api/orders/${id}/review`,{method:"POST",json:{rating,text:$("#revTxt").value}})).order;
      Object.assign(o,x,{_r:0,_t:undefined});render();openOrder(id);toast("Спасибо за отзыв!");return}
    if(a==="cancel"){if(!b.dataset.sure){b.dataset.sure=1;b.textContent=o.data.app?"Точно отозвать?":"Точно отменить?";return}
      Object.assign(o,(await api(`api/orders/${id}/cancel`,{method:"POST"})).order);closeSheet();render();toast(o.data.app?"Заявление отозвано":"Заявка отменена");return}
    if(a==="blank"){showPaper((await api(`api/orders/${id}/blank`)).html,o.svc==="call"?"Справка-вызов":"Бланк заявления");return}
    if(a==="edit"||a==="fix"||a==="again"){closeSheet();openForm(o.svc,a==="again"?null:o,o)}
  }catch(x){toast(x.message)}});

/* ---------- уведомления ---------- */
$("#bell").onclick=async()=>{await reload();
  openSheet(`<div class="sheet-h"><h2>Уведомления</h2><button class="x" data-x aria-label="Закрыть">×</button></div>${NTF.length?NTF.map(n=>`<button class="ntf${n.read?"":" new"}" data-n="${n.oid||0}"><b>${esc(n.title)}</b>${n.body?`<span>${esc(n.body)}</span>`:""}<time>${fmt(n.at)}</time></button>`).join(""):`<p class="note">Уведомлений пока нет</p>`}`);
  if(NTF.some(n=>!n.read)){api("api/notices?program="+encodeURIComponent(prog().id),{method:"POST"}).catch(()=>{});NTF.forEach(n=>n.read=1);renderTop()}};

/* ---------- каталог ---------- */
function renderNew(){if(formState){renderForm();return}
  const L=services().filter(c=>!q||c.t.toLowerCase().includes(q.toLowerCase())),groups=[...new Set(L.map(c=>c.g))];
  $("#vNew").innerHTML=`${ME.foreign_note?`<div class="info" style="margin-bottom:12px"><b>Для иностранных граждан</b> — перечень услуг ниже. ${esc(ME.foreign_note)}</div>`:""}<input class="search" id="q" type="search" placeholder="Поиск: справка, академический отпуск, выписка" value="${esc(q)}" aria-label="Поиск услуги">
   <div class="cat">${groups.map(g=>`<div><h3>${g}</h3><div class="cat-g">${L.filter(c=>c.g===g).map(c=>`<button class="svc" data-svc="${c.id}"><b>${esc(c.t)}</b></button>`).join("")}</div></div>`).join("")||`<p class="empty">Ничего не найдено. Попробуйте другое слово.</p>`}</div>`;
  const qi=$("#q");qi.oninput=()=>{q=qi.value;const pos=qi.selectionStart;renderNew();const n=$("#q");n.focus();n.setSelectionRange(pos,pos)}}
$("#vNew").addEventListener("click",e=>{if(formState)return;const s=e.target.closest("[data-svc]");if(s)openForm(s.dataset.svc)});

/* ---------- формы ---------- */
function openForm(svc,editing,from){tab="new";const src=editing||from,d=src?src.data:{};
  const kinds=prog().kinds||Object.keys(ME.kinds);
  formState={svc,edit:editing?editing.id:null,kind:kinds.includes(d.kind)?d.kind:kinds[0],org:d.org||null,qty:d.qty||1,from:d.from||"",to:d.to||"",ord:d.ord?d.ord.n:"",call:d.call||"",reason:d.reason||"",boss:d.boss||"",
    email:d.email||ME.person.email||"",phone:d.phone||ME.person.phone||"",fields:{...(d.fields||{})},files:[],keep:editing?(d.files||[]):[],step:1,errors:{},busy:false};
  render();window.scrollTo({top:0});
  if(svc==="extract"&&!PRIK[prog().id])api("api/prikazy?program="+encodeURIComponent(prog().id)).then(r=>{PRIK[prog().id]=r.items;if(formState&&formState.svc==="extract")renderForm()}).catch(x=>toast(x.message))}
function field(k,label,inner,o={}){const er=formState.errors[k];return `<div class="f${er?" bad":""}" data-k="${esc(k)}"><label for="f_${esc(k)}">${label}${o.req?' <span class="req" aria-hidden="true">*</span>':""}</label>${inner}${o.note?`<span class="note">${o.note}</span>`:""}${er?`<span class="err">${esc(er)}</span>`:""}</div>`}
function contacts(){const F=formState;return `<div class="two">${field("email","Электронная почта",`<input id="f_email" type="email" autocomplete="email" value="${esc(F.email)}">`,{req:1})}
  ${field("phone","Телефон",`<input id="f_phone" type="tel" autocomplete="tel" value="${esc(F.phone)}" placeholder="+7 900 000-00-00">`)}</div>`}
const qtyErr=n=>!Number.isInteger(n)||n<1?"Укажите количество экземпляров — от 1":n>ME.limit?`Превышен лимит: в одной заявке можно не больше ${ME.limit} бумажных экземпляров (вы указали ${n}). Если нужно больше, подайте ещё одну заявку.`:"";
function qtyField(){return field("qty","Количество экземпляров",`<input id="f_qty" type="number" min="1" max="${ME.limit}" step="1" inputmode="numeric" value="${formState.qty||""}" style="max-width:120px">`,{req:1,note:`От 1 до ${ME.limit} в одной заявке`})}
/* данные заказчика из 1С — подставятся в документ сами */
/* «очной» → «очная», «бюджетной основе» → «бюджетная» — в «Ваших данных» именительный падеж */
const nomin=s=>String(s||"").split(" ")[0].replace(/ой$/,"ая");
function whoBlock(){const p=prog(),P=ME.person;
  return `<div class="who-box"><b>Ваши данные — подставятся автоматически</b><dl class="kv">
    <dt>ФИО</dt><dd>${esc(P.fio)}</dd><dt>Программа</dt><dd>${esc(p.code+" "+p.name)}</dd>
    <dt>Курс, группа</dt><dd>${esc([p.course?p.course+" курс":"","группа "+(p.group||"—")].filter(Boolean).join(", "))}</dd><dt>Институт</dt><dd>${esc(p.inst||"—")}</dd>
    ${p.form?`<dt>Форма обучения</dt><dd>${esc(nomin(p.form))}</dd>`:""}${p.basis?`<dt>Основа обучения</dt><dd>${esc(nomin(p.basis))}</dd>`:""}</dl>
    ${ME.partial?`<span class="note">Часть сведений (институт, форма и основа обучения) 1С пока не передаёт — в документе на их месте будут линии для заполнения.</span>`:""}
    <span class="note">Если здесь ошибка — не отправляйте заявку, напишите или позвоните в Студенческий МФЦ: ${mfcContacts()}</span></div>`}
function orgField(req,label="Для представления (организация)"){const F=formState,o=F.org;
  return field("org",label,o?`<div class="org-pick"><div><b>${esc(o.full)}</b><span>ИНН ${esc(o.inn)}${o.address?", "+esc(o.address):""}</span></div><button class="btn btn-sm" data-a="orgx">Изменить</button></div>`
    :`<div class="org"><input id="f_orgq" placeholder="ИНН (10 или 12 цифр) или название" autocomplete="off" inputmode="search"><div class="org-l" id="orgL" hidden></div></div>`,
    {req,note:o?"Название взято из ЕГРЮЛ — именно так оно будет напечатано в справке":(req?"":"Необязательно: без организации справка выдаётся «по месту требования». ")+"Найдите организацию по ИНН — название подставится из ЕГРЮЛ без ошибок."})}
/* периоды справки-вызова из графика учебного процесса (1С); прошедшие не показываем */
const callSched=()=>(prog().calls||[]).filter(x=>x.to>=isoD(new Date()));
const callPeriods=()=>formState&&formState.reason?callSched().filter(x=>x.reason===formState.reason):[];
const days=(a,b)=>Math.round((pD(b)-pD(a))/864e5)+1;
function receive(){return `<div class="info">Получение — в Студенческом МФЦ, при себе паспорт или студенческий билет.</div>`}
function renderForm(){const F=formState,c=svcOf(F.svc),p=prog();if(!c){formState=null;renderNew();return}
  let h=`<button class="back" data-a="back">← Все услуги</button><h2>${esc(c.t)}</h2>`;
  if(F.edit)h+=`<div class="info">Вы меняете ${c.app?"заявление":"заявку"} № ${F.edit}. Изменить можно, пока статус — «Зарегистрировано» или «Требуется действие».</div>`;
  if(F.svc==="study"){const sg=signer();
    h+=whoBlock()+`<div class="f"><span class="lb">Вид справки <span class="req">*</span></span><div class="seg">${Object.entries(ME.kinds).filter(([k])=>(prog().kinds||[k]).includes(k)).map(([k,x])=>`<button class="opt" data-kind="${k}" aria-pressed="${F.kind===k}"><b>${esc(x.t)}</b><span>${esc(x.d)}</span><em class="where">${esc(x.where||"")}</em></button>`).join("")}</div></div>`;
    const K=ME.kinds[F.kind];h+=K.org==="none"?`<div class="kv-line"><span>Для представления</span><b>по месту требования</b></div>`:orgField(K.org==="required");if(K.qty)h+=qtyField();
    if(F.kind!=="ecp")h+=receive();
    h+=contacts();
  }else if(F.svc==="call"){const per=callPeriods(),it=per.find(x=>x.id===F.call)||(per.length===1?per[0]:null);if(it)F.call=it.id;
    /* все шесть оснований; если по основанию есть период в графике (1С или КУГ) — даты из графика, иначе студент указывает сам */
    h+=whoBlock()+field("reason","Основание вызова",`<select id="f_reason"><option value="">Выберите…</option>${(ME.call_reasons||[]).map(r=>`<option${r===F.reason?" selected":""}>${esc(r)}</option>`).join("")}</select>`,{req:1});
    if(F.reason&&per.length)h+=(per.length>1?field("call","Период по графику учебного процесса",`<select id="f_call"><option value="">Выберите…</option>${per.map(x=>`<option value="${esc(x.id)}"${x.id===F.call?" selected":""}>с ${fmt(x.from)} по ${fmt(x.to)}</option>`).join("")}</select>`,{req:1}):"")
      +(it?`<div class="kv-line"><span>Период вызова</span><b>с ${fmt(it.from)} по ${fmt(it.to)} — ${days(it.from,it.to)} ${plural(days(it.from,it.to),"календарный день","календарных дня","календарных дней")}</b></div>`:"");
    else if(F.reason)h+=`<div class="two">${field("from","Начало",`<input id="f_from" type="date" value="${esc(F.from)}">`,{req:1})}${field("to","Окончание",`<input id="f_to" type="date" value="${esc(F.to)}">`,{req:1})}</div>`;
    h+=orgField(true,"Работодателю")+field("boss","Руководитель организации (ФИО)",`<input id="f_boss" value="${esc(F.boss)}">`)+contacts();
  }else if(["income","income2"].includes(F.svc)){const last=ME.income_last,ly=+last.slice(0,4);
    /* последний месяц — прошлый; текущий появляется сам за ${ME.income_days} дня до конца месяца */
    const opts=v=>{let s="";for(let y=ly-3;y<=ly;y++)for(let m=0;m<12;m++){const k=`${y}-${String(m+1).padStart(2,"0")}`;if(k>last)continue;s+=`<option value="${k}"${k===v?" selected":""}>${MONTHS[m]} ${y}</option>`}return s};
    h+=whoBlock()+`<div class="two">${field("from","Начало периода",`<select id="f_from"><option value="">Выберите…</option>${opts(F.from)}</select>`,{req:1})}${field("to","Окончание периода",`<select id="f_to"><option value="">Выберите…</option>${opts(F.to)}</select>`,{req:1})}</div>`+qtyField()+receive()+contacts();
  }else if(F.svc==="extract"){const L=PRIK[p.id],fq=ME.foreign;
    h+=whoBlock()+field("ord","Приказ",L?`<select id="f_ord"><option value="">Выберите приказ…</option>${L.map(o=>`<option value="${esc(o.n)}"${o.n===F.ord?" selected":""}${o.allowed===false?" disabled":""}>№ ${esc(o.n)} от ${fmt(o.date)}: ${esc(o.title)}${o.allowed===false?" — в Управлении международного сотрудничества":""}</option>`).join("")}</select>`:`<p class="note">Загружаем ваши приказы…</p>`,
      {req:1,note:fq?"Иностранным гражданам МФЦ делает выписки из приказов определённых видов. Остальные — в Управлении международного сотрудничества, 27 учебный корпус":""})
      +(fq?`<div class="kv-line"><span>Экземпляров</span><b>${ME.foreign_qty}: один вам, второй МФЦ передаёт в Управление международного сотрудничества</b></div>`:qtyField())
      +contacts();
  }else{const flds=(ME.forms[c.t]||{}).fields||[];
    h+=`<div class="steps">${["Заполните","Загрузите бланк","Приложите сканированные копии"].map((t,i)=>`<span class="${F.step===i+1?"on":F.step>i+1?"done":""}">${i+1}. ${t}</span>`).join("")}</div>`;
    if(F.step===1){recalc();h+=(ME.foreign_apps_note?`<div class="info">${esc(ME.foreign_apps_note)}</div>`:"")+whoBlock()+flds.map(f=>!shownF(f,F.fields)||f.calc&&f.calc.join?"":f.type==="calc"?field(f.k,esc(f.l),`<div class="calc" id="c_${esc(f.k)}">${F.fields[f.k]?fmt(F.fields[f.k]):"рассчитается автоматически"}</div>`)
        :f.type==="date"&&f.min?(()=>{const low=earliest(f.min);return field(f.k,esc(f.l),`<input id="f_${esc(f.k)}" type="date" min="${isoD(low)}" value="${esc(F.fields[f.k]||"")}">`,{req:f.req,note:f.min.after_today||f.min.today?"":minMsg(f,low)})})()
        :f.type==="select"?(()=>{const L=srcOpts(f,F.fields),off=f.dep&&!F.fields[f.dep];
          return field(f.k,esc(f.l),`<select id="f_${esc(f.k)}"${off?" disabled":""}><option value="">${off?"Сначала выберите: "+esc(((flds.find(x=>x.k===f.dep)||{}).l||"").toLowerCase()):"Выберите…"}</option>${L.map(o=>`<option${F.fields[f.k]===o?" selected":""}>${esc(o)}</option>`).join("")}</select>`,{req:f.req})
          +(f.other&&F.fields[f.k]===f.other?field(f.k+OTHER,otherL(f),`<input id="f_${esc(f.k+OTHER)}" value="${esc(F.fields[f.k+OTHER]||"")}">`,{req:1}):"")})()
        :field(f.k,esc(f.l),f.type==="textarea"?`<textarea id="f_${esc(f.k)}" rows="3">${esc(F.fields[f.k]||"")}</textarea>`
        :`<input id="f_${esc(f.k)}" type="${f.type==="number"?"number":f.type==="date"?"date":"text"}"${f.type==="number"&&nminOf(f,F.fields)!=null?` min="${nminOf(f,F.fields)}"`:""}${nByOff(f,F.fields)?" disabled":""} value="${esc(F.fields[f.k]||"")}"${nPh(f,F.fields,flds)?` placeholder="${esc(nPh(f,F.fields,flds))}"`:""}>`,{req:f.req,note:f.note?esc(f.note):""})).join("")
        +contacts();
    }else if(F.step===2){h+=`<div class="ok">Бланк заполнен. Проверьте данные, подпишите бланк от руки синей ручкой, прикрепите сканированную копию в формате PDF</div>
        <div class="row"><button class="btn btn-go" data-a="blank">Загрузить бланк</button><button class="btn" data-a="s1">← Изменить данные</button></div>`;
    }else{const all=[...F.keep.map((f,i)=>({n:f.name,k:"k"+i})),...F.files.map((f,i)=>({n:f.name,k:"f"+i}))];
      h+=`<div class="f${F.errors.files?" bad":""}"><span class="lb">Подписанное заявление и документы <span class="req">*</span></span>
        <label class="drop" for="f_files">Выберите файлы PDF: подписанное заявление и подтверждающие документы</label><input id="f_files" type="file" accept="application/pdf,.pdf" multiple hidden>
        <div class="files">${all.map((f,i)=>`<div class="file"><span>${i===0?"<b>Заявление:</b> ":""}${esc(f.n)}</span><button class="btn btn-sm" data-rm="${f.k}">Убрать</button></div>`).join("")}</div>
        ${F.errors.files?`<span class="err">${esc(F.errors.files)}</span>`:`<span class="note">Только PDF. До ${ME.uploads.max_files} файлов, каждый до ${ME.uploads.max_mb} МБ, все вместе до ${ME.uploads.max_total_mb} МБ. Первым — подписанное заявление. Скан делайте в 150–200 dpi — так файл получится небольшим.</span>`}</div>
        <div class="row"><button class="btn" data-a="s2">← Назад</button></div>`}
  }
  const last=!c.app||F.step===3;
  h+=`<div class="row" style="margin-top:4px">${c.app&&!last?`<button class="btn btn-go" data-a="nextstep">${F.step===1?"Далее: сформировать бланк":"Далее: приложить скан"}</button>`
     :(F.svc==="study"&&F.kind==="ecp"||F.svc==="call")&&!F.edit?`<button class="btn btn-go" data-a="preview"${F.busy?" disabled":""}>Проверить справку</button>`
     :`<button class="btn btn-go" data-a="submit"${F.busy?" disabled":""}>${F.edit?"Сохранить изменения":c.app?"Отправить заявление":"Отправить заявку"}</button>`}</div>`;
  $("#vNew").innerHTML=`<div class="form${F.svc==="study"?" study-form":""}">${h}</div>`}
function readForm(){const F=formState,v=id=>{const e=document.getElementById("f_"+id);return e?e.value.trim():undefined};
  ["email","phone","from","to","ord","call","reason","boss"].forEach(k=>{const x=v(k);if(x!==undefined)F[k]=x});
  const qq=v("qty");if(qq!==undefined)F.qty=qq===""?0:Number(qq);
  const c=svcOf(F.svc);if(c.app&&F.step===1)((ME.forms[c.t]||{}).fields||[]).forEach(f=>{if(f.type==="calc")return;const x=v(f.k);if(x!==undefined)F.fields[f.k]=x;
    if(f.other){const t=v(f.k+OTHER);if(t!==undefined)F.fields[f.k+OTHER]=t}});recalc()}
function recalc(){const F=formState,c=F&&svcOf(F.svc);if(!c||!c.app)return;
  ((ME.forms[c.t]||{}).fields||[]).forEach(f=>{if(f.type!=="calc")return;F.fields[f.k]=calcVal(f.calc||{},F.fields);
    const el=document.getElementById("c_"+f.k);if(el)el.textContent=F.fields[f.k]?fmt(F.fields[f.k]):"рассчитается автоматически"})}
function check(){const F=formState,c=svcOf(F.svc),E={};
  if(!/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(F.email))E.email="Укажите почту — на неё придут уведомления о заявке";
  const K=F.svc==="study"?ME.kinds[F.kind]:null;
  if((K&&K.org==="required"||F.svc==="call")&&!F.org)E.org="Выберите организацию по ИНН";
  if(F.svc==="call"){if(!F.reason)E.reason="Выберите основание вызова из списка";else if(callPeriods().length){if(!F.call)E.call="Выберите период по графику учебного процесса"}
    else{if(!F.from)E.from="Укажите дату начала";if(!F.to)E.to="Укажите дату окончания";if(F.from&&F.to&&F.to<F.from)E.to="Дата окончания раньше даты начала"}}
  if(["income","income2"].includes(F.svc)){if(!F.from)E.from="Выберите месяц";if(!F.to)E.to="Выберите месяц";if(F.from&&F.to&&F.from>F.to)E.from="Начало периода позже окончания"}
  if(F.svc==="extract"&&!F.ord)E.ord="Выберите приказ из списка";
  if((K&&K.qty)||["income","income2"].includes(F.svc)||(F.svc==="extract"&&!ME.foreign)){const er=qtyErr(F.qty);if(er)E.qty=er}
  if(c.app&&F.step===1)((ME.forms[c.t]||{}).fields||[]).forEach(f=>{if(f.type==="calc"||!shownF(f,F.fields))return;if(f.req&&!F.fields[f.k])E[f.k]="Заполните поле";
    if(f.other&&F.fields[f.k]===f.other&&!F.fields[f.k+OTHER])E[f.k+OTHER]=otherL(f);
    {const n=nminOf(f,F.fields);if(f.type==="number"&&n!=null&&F.fields[f.k]&&/^-?\d+$/.test(F.fields[f.k])&&+F.fields[f.k]<n)E[f.k]=`Не менее ${n}${f.unit?" "+f.unit:""}`}
    if(f.type==="date"&&f.min&&F.fields[f.k]){const low=earliest(f.min);if(F.fields[f.k]<isoD(low))E[f.k]=minMsg(f,low)}});
  if(c.app&&F.step===3&&!F.files.length&&!F.keep.length)E.files="Приложите подписанное заявление (PDF)";
  F.errors=E;return !Object.keys(E).length}
function showErrors(){renderForm();const er=document.querySelector(".f.bad");if(er)er.scrollIntoView({block:"center",behavior:"smooth"})}
const payload=()=>{const F=formState;return {svc:F.svc,program:prog().id,kind:F.kind,inn:F.org?F.org.inn:"",qty:F.qty,from:F.from,to:F.to,ord:F.ord,call:F.call,reason:F.reason,boss:F.boss,
  fields:F.fields,email:F.email,phone:F.phone,keep_files:F.keep.map(f=>f.stored)}};
function showPaper(html,title,confirm){const ecp=formState&&formState.svc==="study";
  openSheet(`<div class="sheet-h"><h2>${esc(title)}</h2><button class="x" data-x aria-label="Закрыть">×</button></div>
   ${confirm?`<div class="info">Так будет выглядеть справка${ecp?"":" (номер и дату присвоит МФЦ при регистрации)"}. Проверьте ФИО, курс, направление, ${ecp?"место предоставления":"организацию и даты"}.</div>
     <div class="warn"><b>Нашли ошибку в своих данных?</b> Не подтверждайте — сообщите в Студенческий МФЦ: ${mfcContacts()}. Данные исправят в 1С, и справка будет верной.</div>`:""}
   <div class="paper" id="printArea">${html}</div>
   ${confirm?`<label class="row" style="align-items:flex-start;flex-wrap:nowrap"><input type="checkbox" id="okChk" style="margin-top:3px"> <span>${ecp?`Данные в справке верны. Отправить справку на подпись: ${esc(signer().fio)}`:"Данные в справке верны. Отправить заявку в МФЦ"}</span></label>
     <div class="row"><button class="btn btn-go" id="okGo" disabled>${ecp?"Подтвердить и отправить на подпись":"Подтвердить и отправить заявку"}</button><button class="btn" data-x>Вернуться к заявке</button></div>`
   :`<div class="row"><button class="btn btn-go" id="prn">Распечатать или сохранить в PDF</button></div><p class="note">В окне печати выберите принтер или «Сохранить как PDF».</p>`}`,true);
  if(confirm){$("#okChk").onchange=e=>$("#okGo").disabled=!e.target.checked;$("#okGo").onclick=()=>{closeSheet();submit()}}
  else $("#prn").onclick=()=>window.print()}
async function submit(){const F=formState;if(F.busy)return;F.busy=true;renderForm();
  const fd=new FormData();fd.append("json",JSON.stringify(payload()));F.files.forEach(f=>fd.append("files",f,f.name));
  try{const o=(await api(F.edit?`api/orders/${F.edit}`:"api/orders",{method:"POST",form:fd})).order;
    const i=ORDERS.findIndex(x=>x.id===o.id);if(i>=0)ORDERS[i]=o;else ORDERS.unshift(o);
    const app=!!o.data.app;formState=null;tab="orders";filter="cur";render();
    toast(F.edit?"Изменения сохранены":o.data.ecp?"Справка отправлена на подпись. Сообщим, когда её можно будет скачать":app?`Заявление № ${num(o)} отправлено в МФЦ`:`Заявка № ${num(o)} отправлена в МФЦ`)}
  catch(x){F.busy=false;if(x.errors){F.errors=x.errors;const c=svcOf(F.svc);
      if(c.app){const f1=((ME.forms[c.t]||{}).fields||[]).some(f=>x.errors[f.k]||x.errors[f.k+OTHER]);F.step=f1?1:3}showErrors();toast(x.message)}else{renderForm();toast(x.message)}}}

$("#vNew").addEventListener("click",async e=>{const F=formState;if(!F)return;
  const k=e.target.closest("[data-kind]");if(k){readForm();F.kind=k.dataset.kind;F.errors={};renderForm();return}
  const rm=e.target.closest("[data-rm]");if(rm){const id=rm.dataset.rm;if(id[0]==="k")F.keep.splice(+id.slice(1),1);else F.files.splice(+id.slice(1),1);renderForm();return}
  const pick=e.target.closest("[data-org]");if(pick&&!pick.disabled){readForm();F.org=(F._orgs||[]).find(o=>o.inn===pick.dataset.org);delete F.errors.org;renderForm();return}
  const b=e.target.closest("[data-a]");if(!b)return;const a=b.dataset.a;readForm();
  if(a==="back"){formState=null;render();return}
  if(a==="orgx"){F.org=null;renderForm();setTimeout(()=>{const x=$("#f_orgq");if(x)x.focus()},0);return}
  if(a==="s1"){F.step=1;renderForm();return}if(a==="s2"){F.step=2;renderForm();return}
  if(a==="blank"){try{showPaper((await api("api/preview",{method:"POST",json:payload()})).html,"Бланк заявления")}catch(x){toast(x.message)}return}
  if(!check()){showErrors();return}
  if(a==="nextstep"){F.step++;renderForm();return}
  if(a==="preview"){try{showPaper((await api("api/preview",{method:"POST",json:payload()})).html,"Проверьте справку перед отправкой",true)}
    catch(x){if(x.errors){F.errors=x.errors;showErrors()}toast(x.message)}return}
  if(a==="submit")submit()});

function innOk(s){if(!/^(\d{10}|\d{12})$/.test(s))return false;const d=s.split("").map(Number),c=w=>w.reduce((a,x,i)=>a+x*d[i],0)%11%10;
  return s.length===10?c([2,4,10,3,5,9,4,6,8])===d[9]:c([7,2,4,10,3,5,9,4,6,8])===d[10]&&c([3,7,2,4,10,3,5,9,4,6,8])===d[11]}
let orgT;
$("#vNew").addEventListener("input",e=>{const F=formState;if(!F)return;
  if(e.target.id==="f_qty"&&+e.target.value>ME.limit){e.target.value=ME.limit;toast(`Не больше ${ME.limit} экземпляров в одной заявке`)}
  if(F.step===1&&svcOf(F.svc).app&&e.target.id&&e.target.id.startsWith("f_")){readForm()}
  if(e.target.id==="f_call"){readForm();delete F.errors.call;renderForm();return}
  if(e.target.id==="f_reason"&&F.svc==="call"){readForm();F.call="";delete F.errors.reason;delete F.errors.call;renderForm();return}
  if(e.target.id==="f_qty"){const n=Number(e.target.value),er=e.target.value===""?"":qtyErr(n),box=e.target.closest(".f");
    box.classList.toggle("bad",!!er);let s=box.querySelector(".err");if(er){if(!s){s=document.createElement("span");s.className="err";box.appendChild(s)}s.textContent=er}else if(s)s.remove();return}
  if(e.target.id!=="f_orgq")return;const v=e.target.value.trim(),L=$("#orgL"),digits=/^\d+$/.test(v);
  const msg=(t,cls="note")=>{L.innerHTML=`<div style="padding:9px 12px" class="${cls}">${t}</div>`;L.hidden=false};
  clearTimeout(orgT);if(v.length<2){L.hidden=true;return}
  if(digits&&(v.length===10||v.length===12)&&!innOk(v)){msg("В ИНН ошибка: не сходится контрольная цифра. Проверьте ИНН — его можно узнать в организации или на её сайте.","err");return}
  if(digits&&v.length>12){msg("ИНН — это 10 цифр у организации или 12 цифр у ИП.","err");return}
  orgT=setTimeout(async()=>{let R=[];try{R=(await api("api/orgs?q="+encodeURIComponent(v))).items}catch(x){msg(esc(x.message),"err");return}
    if(!formState||!$("#orgL"))return;F._orgs=R;const dead=o=>["LIQUIDATED","LIQUIDATING","BANKRUPT"].includes(o.status);
    L.innerHTML=R.length?R.map(o=>`<button type="button" data-org="${esc(o.inn)}"${dead(o)?" disabled":""}><b>${esc(o.name)}</b><span>ИНН ${esc(o.inn)}${o.address?", "+esc(o.address):""}</span>${dead(o)?`<span class="err">Организация ликвидирована по данным ЕГРЮЛ — выбрать нельзя</span>`:""}</button>`).join("")
      :`<div style="padding:9px 12px" class="note">${digits&&v.length>=10?"Организация с таким ИНН не найдена в ЕГРЮЛ. Проверьте цифры.":"Ничего не найдено. Попробуйте поиск по ИНН — так надёжнее."}${ME.demo?"<br>Демо-справочник: введите «7700» или «демо».":""}</div>`;
    L.hidden=false},250)});
$("#vNew").addEventListener("change",e=>{const F0=formState;if(F0&&F0.step===1&&svcOf(F0.svc).app&&e.target.tagName==="SELECT"){readForm();
    const fl=(ME.forms[svcOf(F0.svc).t]||{}).fields||[],k=e.target.id.slice(2),me=fl.find(f=>f.k===k);
    if(me&&(me.other||fl.some(f=>f.dep===k||f.course===k||f.nmin&&f.nmin.by===k||f.show&&f.show.by===k))){let ch=k;fl.forEach(f=>{if(f.dep===ch||f.course===k){if(!srcOpts(f,F0.fields).includes(F0.fields[f.k]))F0.fields[f.k]="";if(f.dep===ch)ch=f.k}});
      delete F0.errors[k];delete F0.errors[k+OTHER];renderForm();const n=document.getElementById(me.other&&F0.fields[k]===me.other?"f_"+k+OTHER:e.target.id);if(n)n.focus()}return}
  if(e.target.id!=="f_files")return;const F=formState,bad=[];
  /* ограничения на файлы проверяются сразу, до загрузки на сервер */
  const U=ME.uploads,MB=1048576;let total=F.keep.reduce((a,f)=>a+(f.size||0),0)+F.files.reduce((a,f)=>a+f.size,0);
  [...e.target.files].forEach(f=>{if(!(/\.pdf$/i.test(f.name)||f.type==="application/pdf"))bad.push(`«${f.name}» — не PDF: можно прикладывать только PDF`);
    else if(f.size>U.max_mb*MB)bad.push(`«${f.name}» больше ${U.max_mb} МБ (${(f.size/MB).toFixed(1)} МБ) — сожмите PDF или сканируйте в 150–200 dpi`);
    else if(F.keep.length+F.files.length>=U.max_files)bad.push(`«${f.name}» не добавлен: не больше ${U.max_files} файлов`);
    else if(total+f.size>U.max_total_mb*MB)bad.push(`«${f.name}» не добавлен: все файлы вместе — не больше ${U.max_total_mb} МБ`);
    else{F.files.push(f);total+=f.size}});
  e.target.value="";F.errors.files=bad.length?bad.join("; "):undefined;readForm();renderForm()});

function render(){if(!ME)return;renderTop();document.querySelectorAll(".tabs button").forEach(b=>b.setAttribute("aria-selected",String(b.dataset.tab===tab)));
  $("#vOrders").hidden=tab!=="orders";$("#vNew").hidden=tab!=="new";if(tab==="orders")renderOrders();else renderNew()}

boot();
/* демо-режим: подсказка с тестовыми учётками на экране входа */
if(!EMBED)fetch("health").then(r=>r.json()).then(h=>{if(h.demo){$("#lDemo").hidden=false;$("#lDemo").textContent="Демо-режим. Студенты: st100001–st100005 (st100005 — иностранный гражданин), сотрудник: mfc01, администратор: mfcadmin. Пароль: demo2026."}}).catch(()=>{});

/* ---------- «Есть вопрос»: письмо в МФЦ с данными студента, звонок ---------- */
$("#askBtn").onclick=()=>{const own=ORDERS.filter(o=>!o.archived);
  openSheet(`<div class="sheet-h"><h2>Есть вопрос</h2><button class="x" data-x aria-label="Закрыть">×</button></div>
   <p class="note">Напишите вопрос — откроется письмо в Студенческий МФЦ, ваши ФИО, программа и группа подставятся сами. Или позвоните.</p>
   <div class="f"><label for="askT">Тема</label><select id="askT"><option>Вопрос по заявке</option><option>Справки и выписки</option><option>Заявления</option><option>Вход в личный кабинет</option><option>Другое</option></select></div>
   ${own.length?`<div class="f"><label for="askO">Заявка (если вопрос по ней)</label><select id="askO"><option value="">— не выбран —</option>${own.map(o=>`<option value="${o.id}">№ ${esc(num(o))} — ${esc(o.title)}</option>`).join("")}</select></div>`:""}
   <div class="f"><label for="askQ">Вопрос</label><textarea id="askQ" rows="4" placeholder="Опишите, что нужно"></textarea></div>
   <div class="row"><a class="btn btn-go" id="askGo" href="#">Написать в МФЦ</a><a class="btn" href="tel:${MFC.phone}">Позвонить: ${MFC.phoneT}</a></div>
   <p class="note">Почта: <a href="mailto:${MFC.email}">${MFC.email}</a></p>`);
  $("#askGo").onclick=e=>{const t=$("#askT").value,oid=$("#askO")?$("#askO").value:"",o=ORDERS.find(x=>String(x.id)===oid),p=prog()||{};
    const subj=`${t}${o?" № "+num(o):""} — ${ME.name||ME.person.fio}`;
    const body=[$("#askQ").value.trim(),"","—",`ФИО: ${ME.person.fio}`,`Программа: ${[p.code,p.name].filter(Boolean).join(" ")}, ${p.course||""} курс, группа ${p.group||""}`,
      o?`Заявка: № ${num(o)} — ${o.title} (${ST[o.status][0]})`:"",ME.person.phone?`Телефон: ${ME.person.phone}`:"",ME.person.email?`Почта: ${ME.person.email}`:""].filter((x,i)=>x||i<3).join("\n");
    e.currentTarget.href=`mailto:${MFC.email}?subject=${encodeURIComponent(subj)}&body=${encodeURIComponent(body)}`}};

})();
