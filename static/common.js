// 세 화면이 같이 쓰는 함수와 머리글·바닥글 (한 곳에서 그려 세 화면을 똑같이 유지)
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const today = () => new Date().toLocaleDateString("sv-SE");   // 로컬(한국) 날짜 YYYY-MM-DD

const PAGES = [["/", "습득물 등록"], ["/lost_new.html", "분실물 신고"], ["/lost.html", "분실물 확인"]];
const ICON = {
  search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
  login: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10 17l5-5-5-5M15 12H3M14 4h5v16h-5"/></svg>',
};

function renderChrome() {
  const here = location.pathname === "/index.html" ? "/" : location.pathname;
  const title = PAGES.find(([p]) => p === here)?.[1] || "";
  document.body.insertAdjacentHTML("afterbegin", `
    <div class="masthead"><div class="inner"><b>제안 시안</b> 습득물 공고 표준화 멀티에이전트 PoC 화면입니다. 실제 경찰민원24 서비스가 아닙니다.</div></div>
    <header class="site-header">
      <div class="inner">
        <div class="header-utility"><span>누리집 안내지도</span><span>글자·화면 설정</span><span>Language</span></div>
        <div class="header-branding">
          <a class="logo" href="/" aria-label="경찰민원24 (제안 시안)"></a>
          <div class="header-actions"><button class="btn-navi">${ICON.search}통합검색</button><button class="btn-navi">${ICON.login}로그인</button></div>
        </div>
      </div>
      <nav class="main-menu"><div class="inner"><ul class="gnb">
        <li><a class="selected" href="/">민원서비스</a></li><li><a href="#">국민신문고(신고·제보)</a></li><li><a href="#">고객센터</a></li>
      </ul></div></nav>
    </header>`);
  const crumb = $("crumb");
  if (crumb) crumb.outerHTML = `<nav class="breadcrumb"><a href="/">홈</a><span class="sep">&gt;</span><a href="/">민원서비스</a><span class="sep">&gt;</span><a href="/">민원 신청·안내</a><span class="sep">&gt;</span><span>${esc(title)}</span></nav>`;
  const tabs = $("tabs");
  if (tabs) tabs.outerHTML = `<div class="tabs">${PAGES.map(([p, t]) => `<a href="${p}" class="${p === here ? "on" : ""}">${t}</a>`).join("")}</div>`;
  document.body.insertAdjacentHTML("beforeend", `
    <footer><div class="inner">
      <div class="f-links"><span>경찰청/부속기관 ∨</span><span>업무관련사이트 ∨</span></div>
      <div class="f-body"><div class="f-logo"></div><div class="f-info">
        <p>(03739) 서울특별시 서대문구 통일로 97</p>
        <p><b>대표전화</b>182 (유료, 평일 09시-18시) &nbsp; <b>해외이용</b>+82-182 (유료, 평일 09시-18시)</p>
        <div class="f-menu"><span>이용안내</span><span>찾아오시는 길</span><span class="pv">개인정보처리방침</span><span>저작권 정책</span></div>
        <p class="copy">제안 시안 · 습득물 공고 표준화 멀티에이전트 PoC (LangGraph + MCP)</p>
      </div></div>
    </div></footer>`);
}
renderChrome();

// 대분류·소분류 선택 상자 채우기 (분류는 서버 agents/categories.py 하나만 기준)
async function fillCategories(mainSel, subSel) {
  const data = await (await fetch("/api/categories")).json();
  const { categories } = data;
  mainSel.innerHTML = '<option value="">대분류 선택</option>' +
    Object.keys(categories).map((c) => `<option>${esc(c)}</option>`).join("");
  const fillSub = () => {
    subSel.innerHTML = '<option value="">소분류 선택</option>' +
      (categories[mainSel.value] || []).map((c) => `<option>${esc(c)}</option>`).join("");
  };
  mainSel.onchange = fillSub;
  fillSub();
  return data;                                     // { categories, checks, colors }
}
