// ============================================================
// 明石高専 学食ナビ - フロントエンド (Render API連携)
// ============================================================

const state = {
  token: localStorage.getItem("gakushoku_token") || null,
  role: localStorage.getItem("gakushoku_role") || null,
  identity: localStorage.getItem("gakushoku_identity") || null,
  menus: [],
  currentSort: "price",
};

let toastTimer = null;

// ------------------------------------------------------------------
// ユーティリティ
// ------------------------------------------------------------------
function $(id){ return document.getElementById(id); }

function showToast(msg){
  const t = $("toast");
  t.textContent = msg;
  t.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(()=> t.classList.remove("show"), 2400);
}

function showView(id){
  document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
  $(id).classList.add("active");
  window.scrollTo({top:0, behavior:"instant"});
}

async function apiFetch(path, { method="GET", body=null, auth=false, isForm=false } = {}){
  const headers = {};
  if(!isForm) headers["Content-Type"] = "application/json";
  if(auth && state.token) headers["Authorization"] = `Bearer ${state.token}`;

  const res = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: isForm ? body : (body ? JSON.stringify(body) : undefined),
  });

  let data = null;
  try{ data = await res.json(); } catch(e){ /* no body */ }

  if(!res.ok){
    throw new Error((data && data.error) || `通信エラーが発生しました (${res.status})`);
  }
  return data;
}

function starString(avg, count){
  if(avg === null || avg === undefined) return "評価なし";
  const rounded = Math.round(avg);
  return "★".repeat(rounded) + "☆".repeat(5-rounded) + `  (${avg.toFixed(1)} / ${count}件)`;
}

// ------------------------------------------------------------------
// ログイン状態管理
// ------------------------------------------------------------------
function persistAuth(){
  if(state.token){
    localStorage.setItem("gakushoku_token", state.token);
    localStorage.setItem("gakushoku_role", state.role);
    localStorage.setItem("gakushoku_identity", state.identity);
  } else {
    localStorage.removeItem("gakushoku_token");
    localStorage.removeItem("gakushoku_role");
    localStorage.removeItem("gakushoku_identity");
  }
}

async function goHome(){
  if(!state.token){ showView("view-login"); return; }
  if(state.role === "student"){
    await renderStudentTop();
    showView("view-student-top");
  } else {
    await renderAdminTop();
    showView("view-admin-top");
  }
}

async function loginStudent(){
  const student_id = $("student-id-input").value.trim();
  const password = $("student-pw-input").value;
  if(!student_id || !password){ showToast("学籍番号とパスワードを入力してください"); return; }
  if(student_id.length < 5 || student_id.length > 6){ showToast("学籍番号は5桁または6桁で入力してください"); return; }
  try{
    const data = await apiFetch("/api/auth/login", { method:"POST", body:{ student_id, password } });
    state.token = data.token; state.role = "student"; state.identity = data.student_id;
    persistAuth();
    afterLogin();
  }catch(e){ showToast(e.message); }
}

async function registerStudent(){
  const student_id = $("reg-id-input").value.trim();
  const email = $("reg-email-input").value.trim();
  const password = $("reg-pw-input").value;
  if(student_id.length < 5 || student_id.length > 6){ showToast("学籍番号は5桁または6桁で入力してください"); return; }
  if(!email || !email.includes("@")){ showToast("有効なメールアドレスを入力してください"); return; }
  try{
    const data = await apiFetch("/api/auth/register", { method:"POST", body:{ student_id, email, password } });
    showToast(data.message || "確認メールを送信しました。メール内のリンクをクリックしてください");
    $("student-id-input").value = student_id;
    document.querySelectorAll(".auth-panel").forEach(p=>p.classList.remove("active"));
    $("login-student").classList.add("active");
  }catch(e){ showToast(e.message); }
}

async function resendVerification(){
  const student_id = $("student-id-input").value.trim();
  if(!student_id){ showToast("学籍番号を入力してから押してください"); return; }
  try{
    const data = await apiFetch("/api/auth/resend-verification", { method:"POST", body:{ student_id } });
    showToast(data.message || "確認メールを再送信しました");
  }catch(e){ showToast(e.message); }
}

async function loginAdmin(){
  const password = $("admin-pw-input").value;
  try{
    const data = await apiFetch("/api/auth/admin-login", { method:"POST", body:{ password } });
    state.token = data.token; state.role = "admin"; state.identity = "admin";
    persistAuth();
    afterLogin();
  }catch(e){ showToast(e.message); }
}

function afterLogin(){
  $("app-header").classList.remove("hidden");
  $("role-badge").textContent = state.role === "student" ? "学生" : "管理者";
  showToast("ログインしました");
  goHome();
}

function logout(){
  state.token = null; state.role = null; state.identity = null;
  persistAuth();
  $("app-header").classList.add("hidden");
  showView("view-login");
}

// ------------------------------------------------------------------
// 学生: メニュー確認画面(ランキング)
// ------------------------------------------------------------------
const SORT_LABELS = {
  price: "値段が安い順に表示します。",
  efficiency: "1円あたりのカロリーが高い順に表示します。",
  rating: "レビュー評価が高い順に表示します。",
};

function todayDateString(){
  const d = new Date();
  const pad = n => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth()+1)}-${pad(d.getDate())}`;
}

async function fetchMenus(dateFilter){
  const qs = dateFilter ? `?date=${dateFilter}` : "";
  state.menus = await apiFetch(`/api/menus${qs}`);
  return state.menus;
}

async function fetchCongestion(){
  return apiFetch("/api/congestion");
}

function sortedMenus(sortKey){
  const onSale = state.menus.filter(m => !m.soldout_status);
  const arr = [...onSale];
  if(sortKey === "price") arr.sort((a,b)=> a.price - b.price);
  if(sortKey === "efficiency") arr.sort((a,b)=> (b.calorie/b.price) - (a.calorie/a.price));
  if(sortKey === "rating") arr.sort((a,b)=> {
    const ra = a.avg_rating; const rb = b.avg_rating;
    return (rb === null ? -1 : rb) - (ra === null ? -1 : ra);
  });
  return arr;
}

function renderMenuGrid(){
  const grid = $("menu-grid");
  const list = sortedMenus(state.currentSort);
  if(list.length === 0){
    grid.innerHTML = `<p class="empty-note">表示できるメニューがありません。</p>`;
    return;
  }
  grid.innerHTML = list.map(m => {
    const efficiency = (m.calorie / m.price).toFixed(2);
    return `
      <div class="menu-card">
        <span class="name">${m.menu_name}</span>
        <span class="cat">カテゴリ: ${m.category || "-"}${m.date ? ` / ${m.date}限定` : ""}</span>
        <span class="row"><span>値段</span><span>${m.price}円</span></span>
        <span class="row"><span>カロリー</span><span>${m.calorie}kcal</span></span>
        <span class="row"><span>1円当たり</span><span>${efficiency}kcal</span></span>
        <span class="stars">${starString(m.avg_rating, m.review_count)}</span>
        <div style="display:flex; gap:8px; margin-top:8px;">
          <button class="review-btn" data-menu-id="${m.id}">評価する</button>
          <button class="review-btn" data-view-id="${m.id}" data-view-name="${m.menu_name}">レビューを見る</button>
        </div>
      </div>
    `;
  }).join("");

  grid.querySelectorAll("[data-menu-id]").forEach(btn=>{
    btn.addEventListener("click", ()=>{
      openReviewFor(Number(btn.dataset.menuId));
    });
  });
  grid.querySelectorAll("[data-view-id]").forEach(btn=>{
    btn.addEventListener("click", ()=>{
      openMenuReviews(Number(btn.dataset.viewId), btn.dataset.viewName);
    });
  });
}

async function renderStatusStrip(){
  const congestion = await fetchCongestion();
  const soldOutList = state.menus.filter(m => m.soldout_status).map(m => m.menu_name);
  $("status-strip").innerHTML = `
    <div class="status-chip">
      <span class="label">食堂の現在状況</span>
      <span class="value">${congestion.label}${congestion.date ? `(最終更新: ${new Date(congestion.date).toLocaleString("ja-JP")})` : ""}</span>
    </div>
    <div class="status-chip">
      <span class="label">売り切れ中のメニュー</span>
      <span class="value">${soldOutList.length ? soldOutList.join("、") : "なし"}</span>
    </div>
  `;
}

async function renderStudentTop(){
  $("menu-grid").innerHTML = `<p class="empty-note">読み込み中...</p>`;
  try{
    await fetchMenus(todayDateString());
    await renderStatusStrip();
    renderMenuGrid();
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// 学生: 満席・売り切れ確認/報告画面
// ------------------------------------------------------------------
async function renderSoldOutView(){
  try{
    await fetchMenus(todayDateString());
    const congestion = await fetchCongestion();
    const soldOutList = state.menus.filter(m => m.soldout_status).map(m => m.menu_name);

    $("info-congestion").textContent = `食堂の現在状況: ${congestion.label}${congestion.date ? `(最終更新: ${new Date(congestion.date).toLocaleString("ja-JP")})` : ""}`;
    $("info-soldout").textContent = `売り切れ報告: ${soldOutList.length ? soldOutList.join("、") : "なし"}`;
    $("select-congestion").value = congestion.label;

    const sel = $("select-soldout-menu");
    sel.innerHTML = state.menus.map(m => `<option value="${m.id}">${m.menu_name}${m.soldout_status ? "(売り切れ中)":""}</option>`).join("");
  }catch(e){ showToast(e.message); }
}

async function reportCongestion(){
  try{
    await apiFetch("/api/congestion/report", { method:"POST", auth:true, body:{ status: $("select-congestion").value } });
    showToast("混雑状況を報告しました");
    renderSoldOutView();
  }catch(e){ showToast(e.message); }
}

async function reportSoldOut(){
  const menuId = Number($("select-soldout-menu").value);
  const status = $("select-soldout-status").value;
  try{
    const menu = await apiFetch(`/api/menus/${menuId}/soldout`, {
      method:"POST", auth:true, body:{ soldout: status === "売り切れ" },
    });
    showToast(`「${menu.menu_name}」の状況を報告しました`);
    renderSoldOutView();
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// 学生: メニュー評価画面
// ------------------------------------------------------------------
async function renderReviewView(preselectId){
  try{
    if(state.menus.length === 0) await fetchMenus(todayDateString());
    const sel = $("select-review-menu");
    sel.innerHTML = state.menus.map(m => `<option value="${m.menu_name}">${m.menu_name}</option>`).join("");
    if(preselectId){
      const m = state.menus.find(mm => mm.id === preselectId);
      if(m) sel.value = m.menu_name;
    }
    $("select-review-rating").value = "3";
    document.querySelectorAll("#low-rating-tags input[type=checkbox]").forEach(cb => cb.checked = false);
    $("review-comment").value = "";
    toggleLowRatingTags();
  }catch(e){ showToast(e.message); }
}

function toggleLowRatingTags(){
  const rating = Number($("select-review-rating").value);
  $("low-rating-tags").style.display = rating <= 2 ? "block" : "none";
}

async function openMenuReviews(menuId, menuName){
  $("menu-reviews-title").textContent = `${menuName} のレビュー`;
  $("menu-reviews-list").innerHTML = `<p class="empty-note">読み込み中...</p>`;
  showView("view-menu-reviews");
  try{
    const data = await apiFetch(`/api/menus/${menuId}/reviews`);
    if(data.reviews.length === 0){
      $("menu-reviews-list").innerHTML = `<p class="empty-note">まだレビューがありません。</p>`;
      return;
    }
    $("menu-reviews-list").innerHTML = data.reviews.map(r => `
      <div class="review-item">
        <div class="top-row">
          <span class="menu-name">匿名の学生</span>
          <span class="stars">${"★".repeat(r.review_score)}${"☆".repeat(5-r.review_score)}</span>
        </div>
        <div class="meta">${new Date(r.created_at).toLocaleDateString("ja-JP")}</div>
        ${r.review_tag.length ? `<div class="tags">${r.review_tag.map(t=>`<span class="tag">${t}</span>`).join("")}</div>` : ``}
        ${r.review_msg ? `<div class="comment">${r.review_msg}</div>` : ``}
      </div>
    `).join("");
  }catch(e){ showToast(e.message); }
}

async function openReviewFor(menuId){
  await renderReviewView(menuId);
  showView("view-review");
}

async function submitReview(){
  const menu_name = $("select-review-menu").value;
  const review_score = Number($("select-review-rating").value);
  const tags = Array.from(document.querySelectorAll("#low-rating-tags input:checked")).map(cb => cb.value);
  const review_msg = $("review-comment").value.trim();

  try{
    await apiFetch("/api/reviews", {
      method:"POST", auth:true,
      body:{ menu_name, review_score, review_msg, review_tag: tags },
    });
    showToast(`「${menu_name}」の評価を送信しました`);
    await renderStudentTop();
    showView("view-student-top");
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// 管理者: メニュー編集画面(トップ)
// ------------------------------------------------------------------
async function renderAdminTop(){
  $("admin-menu-tbody").innerHTML = `<tr><td colspan="7">読み込み中...</td></tr>`;
  try{
    const filterDate = $("admin-date-filter").value; // 空なら全件
    await fetchMenus(filterDate || null);
    $("admin-menu-tbody").innerHTML = state.menus.map(m => `
      <tr data-id="${m.id}">
        <td class="name-cell">${m.menu_name}</td>
        <td><input type="text" class="edit-category" value="${m.category || ""}"></td>
        <td><input type="number" class="edit-price" value="${m.price}" min="0"></td>
        <td><input type="number" class="edit-calorie" value="${m.calorie}" min="0"></td>
        <td><input type="date" class="edit-date" value="${m.date || ""}"></td>
        <td>
          <select class="edit-onsale">
            <option value="true" ${!m.soldout_status ? "selected":""}>はい</option>
            <option value="false" ${m.soldout_status ? "selected":""}>いいえ</option>
          </select>
        </td>
        <td><button class="delete-btn" data-menu-id="${m.id}">削除</button></td>
      </tr>
    `).join("");

    $("admin-menu-tbody").querySelectorAll(".delete-btn").forEach(btn=>{
      btn.addEventListener("click", ()=> deleteMenu(Number(btn.dataset.menuId), btn));
    });
  }catch(e){ showToast(e.message); }
}

async function deleteAllMenus(){
  if(!confirm("本当に全てのメニューを削除しますか?この操作は取り消せません。")) return;
  try{
    const data = await apiFetch("/api/admin/menus", { method:"DELETE", auth:true });
    showToast(data.message || "全メニューを削除しました");
    renderAdminTop();
  }catch(e){ showToast(e.message); }
}

async function deleteMenu(menuId, btnEl){
  const row = btnEl.closest("tr");
  const name = row.querySelector(".name-cell").textContent;
  if(!confirm(`「${name}」を削除します。よろしいですか?`)) return;
  try{
    await apiFetch(`/api/admin/menus/${menuId}`, { method:"DELETE", auth:true });
    showToast(`「${name}」を削除しました`);
    renderAdminTop();
  }catch(e){ showToast(e.message); }
}

async function saveMenus(){
  const rows = $("admin-menu-tbody").querySelectorAll("tr[data-id]");
  try{
    for(const row of rows){
      const id = Number(row.dataset.id);
      await apiFetch(`/api/admin/menus/${id}`, {
        method:"PATCH", auth:true,
        body:{
          category: row.querySelector(".edit-category").value.trim(),
          price: Number(row.querySelector(".edit-price").value) || 0,
          calorie: Number(row.querySelector(".edit-calorie").value) || 0,
          date: row.querySelector(".edit-date").value || "",
          on_sale: row.querySelector(".edit-onsale").value === "true",
        },
      });
    }
    showToast("変更を保存しました");
  }catch(e){ showToast(e.message); }
}

async function uploadCsv(){
  const fileInput = $("csv-file-input");
  if(!fileInput.files.length){ showToast("CSVファイルを選択してください"); return; }
  const formData = new FormData();
  formData.append("file", fileInput.files[0]);
  try{
    const data = await apiFetch("/api/admin/menus/bulk", { method:"POST", auth:true, isForm:true, body:formData });
    showToast(data.message || "アップロードしました");
    fileInput.value = "";
    renderAdminTop();
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// 管理者: 新規メニュー追加画面
// ------------------------------------------------------------------
function clearAddForm(){
  $("add-name").value = "";
  $("add-category").value = "";
  $("add-price").value = "";
  $("add-calorie").value = "";
  $("add-date").value = "";
  $("add-onsale").value = "true";
}

async function addMenu(){
  const menu_name = $("add-name").value.trim();
  const category = $("add-category").value.trim();
  const price = Number($("add-price").value);
  const calorie = Number($("add-calorie").value);
  const date = $("add-date").value || null;
  const on_sale = $("add-onsale").value === "true";

  if(!menu_name || !category || !price || !calorie){
    showToast("メニュー名・カテゴリ・値段・カロリーを入力してください");
    return;
  }

  try{
    await apiFetch("/api/admin/menus", {
      method:"POST", auth:true,
      body:{ menu_name, category, price, calorie, date, on_sale },
    });
    showToast(`「${menu_name}」を追加しました`);
    clearAddForm();
    await renderAdminTop();
    showView("view-admin-top");
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// 管理者: レビュー管理画面
// ------------------------------------------------------------------
async function renderAdminReviews(){
  const list = $("admin-review-list");
  list.innerHTML = `<p class="empty-note">読み込み中...</p>`;
  try{
    const reviews = await apiFetch("/api/reviews", { auth:true });
    if(reviews.length === 0){
      list.innerHTML = `<p class="empty-note">レビューはまだありません。</p>`;
      return;
    }
    list.innerHTML = reviews.map(r => `
      <div class="review-item" data-id="${r.id}">
        <div class="top-row">
          <span class="menu-name">${r.menu_name}</span>
          <span class="stars">${"★".repeat(r.review_score)}${"☆".repeat(5-r.review_score)}</span>
        </div>
        <div class="meta">${new Date(r.created_at).toLocaleString("ja-JP")} / 学籍番号: ${r.reviewer_id || "-"}</div>
        ${r.review_tag.length ? `<div class="tags">${r.review_tag.map(t=>`<span class="tag">${t}</span>`).join("")}</div>` : ``}
        ${r.review_msg ? `<div class="comment">${r.review_msg}</div>` : ``}
        <button class="delete-btn" data-review-id="${r.id}" style="margin-top:8px;">このレビューを削除</button>
      </div>
    `).join("");

    list.querySelectorAll(".delete-btn").forEach(btn=>{
      btn.addEventListener("click", async ()=>{
        try{
          await apiFetch(`/api/admin/reviews/${btn.dataset.reviewId}`, { method:"DELETE", auth:true });
          showToast("レビューを削除しました");
          renderAdminReviews();
        }catch(e){ showToast(e.message); }
      });
    });
  }catch(e){ showToast(e.message); }
}

// ------------------------------------------------------------------
// イベント登録
// ------------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {

  // ログイン画面タブ切り替え
  document.querySelectorAll(".auth-tab").forEach(tab=>{
    tab.addEventListener("click", ()=>{
      document.querySelectorAll(".auth-tab").forEach(t=>t.classList.remove("active"));
      document.querySelectorAll(".auth-panel").forEach(p=>p.classList.remove("active"));
      tab.classList.add("active");
      $(tab.dataset.target).classList.add("active");
    });
  });
  $("btn-show-register").addEventListener("click", ()=>{
    document.querySelectorAll(".auth-panel").forEach(p=>p.classList.remove("active"));
    $("login-register").classList.add("active");
  });
  $("btn-show-login").addEventListener("click", ()=>{
    document.querySelectorAll(".auth-panel").forEach(p=>p.classList.remove("active"));
    $("login-student").classList.add("active");
  });

  $("btn-login-student").addEventListener("click", loginStudent);
  $("btn-register").addEventListener("click", registerStudent);
  $("btn-resend-verification").addEventListener("click", resendVerification);
  $("btn-login-admin").addEventListener("click", loginAdmin);
  $("btn-logout").addEventListener("click", logout);
  $("btn-go-home").addEventListener("click", goHome);

  $("btn-goto-soldout").addEventListener("click", ()=>{ renderSoldOutView(); showView("view-soldout"); });
  $("btn-goto-review-blank").addEventListener("click", ()=>{ renderReviewView(); showView("view-review"); });

  $("rank-tabs").addEventListener("click", (e)=>{
    const btn = e.target.closest(".tab");
    if(!btn) return;
    document.querySelectorAll("#rank-tabs .tab").forEach(t=>t.classList.remove("active"));
    btn.classList.add("active");
    state.currentSort = btn.dataset.sort;
    $("tab-desc").textContent = SORT_LABELS[state.currentSort];
    renderMenuGrid();
  });

  $("btn-back-from-soldout").addEventListener("click", goHome);
  $("btn-report-congestion").addEventListener("click", reportCongestion);
  $("btn-report-soldout").addEventListener("click", reportSoldOut);

  $("btn-back-from-review").addEventListener("click", goHome);
  $("select-review-rating").addEventListener("change", toggleLowRatingTags);
  $("btn-submit-review").addEventListener("click", submitReview);

  $("btn-goto-admin-add").addEventListener("click", ()=>{ clearAddForm(); showView("view-admin-add"); });
  $("btn-goto-admin-reviews").addEventListener("click", ()=>{ renderAdminReviews(); showView("view-admin-reviews"); });
  $("btn-save-menus").addEventListener("click", saveMenus);
  $("btn-reset-menus").addEventListener("click", renderAdminTop);
  $("btn-csv-upload").addEventListener("click", uploadCsv);
  $("btn-delete-all-menus").addEventListener("click", deleteAllMenus);
  $("btn-filter-today").addEventListener("click", ()=>{ $("admin-date-filter").value = todayDateString(); renderAdminTop(); });
  $("btn-filter-all").addEventListener("click", ()=>{ $("admin-date-filter").value = ""; renderAdminTop(); });

  $("btn-back-from-menu-reviews").addEventListener("click", goHome);

  $("btn-back-from-add").addEventListener("click", ()=>{ renderAdminTop(); showView("view-admin-top"); });
  $("btn-add-menu").addEventListener("click", addMenu);

  $("btn-back-from-reviews").addEventListener("click", ()=>{ renderAdminTop(); showView("view-admin-top"); });

  // 自動ログイン(トークンが残っている場合)
  if(state.token && state.role){
    $("app-header").classList.remove("hidden");
    $("role-badge").textContent = state.role === "student" ? "学生" : "管理者";
    goHome();
  } else {
    showView("view-login");
  }
});
