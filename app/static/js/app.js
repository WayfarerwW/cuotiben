/**
 * 错题本 · Vue 3 应用主逻辑
 * ---------------------------------------------------------------------------
 * 约定（AGENTS.md）：
 *   - 3.2 组件状态切换不用 vue-router：页面由 currentPage 这个 ref 决定
 *   - 3.2 API 一律走 js/api.js，本文件不直接 fetch
 *   - 3.2 视觉参数只能用 css/style.css 里的设计令牌，不写死色值间距
 *   - 4.1 打勾：任何题任何时间都能打、interval_index 归零、允许重复打勾
 *   - 4.3 通知面板内点击列表项**在面板内展开复习视图**，不跳页
 *   - 4.5 草稿保护：四个 key + 500ms 防抖 + 进入页面提示恢复 + 提交后清除
 *
 * 用 Vue 3 Composition API（setup + ref/computed），无构建工具，直接跑原生 ESM-less 全局构建。
 */

(function () {
  'use strict';

  var createApp = Vue.createApp;
  var ref = Vue.ref;
  var computed = Vue.computed;
  var reactive = Vue.reactive;
  var watch = Vue.watch;
  var onMounted = Vue.onMounted;
  var onUnmounted = Vue.onUnmounted;
  var nextTick = Vue.nextTick;

  /* ======================================================================
   * 常量
   * ==================================================================== */

  /** 草稿 key（AGENTS.md 4.5 规定四个）。 */
  var DRAFT_KEYS = {
    questionNew: 'draft_question_new',
    question: function (id) { return 'draft_question_' + id; },
    noteNew: 'draft_note_new',
    note: function (id) { return 'draft_note_' + id; },
  };

  var DRAFT_DEBOUNCE_MS = 500;

  var MASTERY_LABEL = { still_wrong: '仍易错', mastered: '已拿下' };

  var NAV_ITEMS = [
    { key: 'home', label: '首页', icon: 'i-home' },
    { key: 'questions', label: '题目管理', icon: 'i-questions' },
    { key: 'review', label: '今日复习', icon: 'i-review' },
    { key: 'notes', label: '记事本', icon: 'i-notes' },
    { key: 'stats', label: '统计分析', icon: 'i-stats' },
    { key: 'settings', label: '设置', icon: 'i-settings' },
  ];

  var PAGE_TITLES = {
    home: '首页概览',
    questions: '题目管理',
    review: '今日复习',
    notes: '记事本',
    stats: '统计分析',
    settings: '设置',
    'about-data': '数据说明',
  };

  var VALID_PAGES = Object.keys(PAGE_TITLES);

  /** 打勾时可选的四档掌握程度（ui-design 4.3）。 */
  var MASTERY_CHOICES = [
    { value: 0, label: '完全不会' },
    { value: 1, label: '有点模糊' },
    { value: 2, label: '基本掌握' },
    { value: 3, label: '已掌握' },
  ];

  /* ======================================================================
   * 工具
   * ==================================================================== */

  /** ISO 8601 -> 本地可读。空值给 —。 */
  function fmtDate(iso) {
    if (!iso) { return '—'; }
    var d = new Date(iso);
    if (isNaN(d.getTime())) { return '—'; }
    var now = Date.now();
    var diff = now - d.getTime();
    if (diff < 60 * 1000) { return '刚刚'; }
    if (diff < 60 * 60 * 1000) { return Math.floor(diff / 60000) + ' 分钟前'; }
    if (diff < 24 * 60 * 60 * 1000) { return Math.floor(diff / 3600000) + ' 小时前'; }
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }

  function pad(n) { return n < 10 ? '0' + n : String(n); }

  /** localStorage 包装：隐私模式或超限时不能让它把整个应用带崩。 */
  var storage = {
    get: function (key) {
      try { return window.localStorage.getItem(key); } catch (e) { return null; }
    },
    set: function (key, value) {
      try { window.localStorage.setItem(key, value); return true; }
      catch (e) { return false; }
    },
    remove: function (key) {
      try { window.localStorage.removeItem(key); } catch (e) { /* 忽略 */ }
    },
    keys: function () {
      var out = [];
      try {
        for (var i = 0; i < window.localStorage.length; i += 1) {
          var k = window.localStorage.key(i);
          if (k && k.indexOf('draft_') === 0) { out.push(k); }
        }
      } catch (e) { /* 忽略 */ }
      return out;
    },
  };

  /* ======================================================================
   * 应用
   * ==================================================================== */

  var App = {
    setup: function () {
      /* ---------------- 页面路由（组件内状态，不用 vue-router）--------------- */
      var currentPage = ref(pageFromHash());
      var loading = ref(false);
      var loadError = ref('');
      var sidebarCollapsed = ref(false);
      var drawerOpen = ref(false);

      /* ---------------- 数据 ---------------- */
      var folderTree = ref([]);
      var questions = ref([]);
      var tags = ref([]);
      var reviewQueue = ref([]);
      var health = ref({});
      var backfillStats = ref({ today_backfill_count: 0, consecutive_days: 0, backlog_count: 0 });
      var stats = ref({ total: 0, due: 0, mastered: 0, wrong: 0 });
      var reviewDoneToday = ref(0);

      /* ---------------- 筛选 ---------------- */
      var currentFolderId = ref(null);
      var filterType = ref('all');
      var tableSearch = ref('');
      var searchDebounce = null;

      var filterOptions = [
        { value: 'all', label: '全部' },
        { value: 'starred', label: '重点' },
        { value: 'wrong', label: '仍易错' },
        { value: 'mastered', label: '已掌握' },
      ];

      var expandedFolders = reactive({});

      /* ---------------- 通知面板 ---------------- */
      var notifOpen = ref(false);
      var userMenuOpen = ref(false);
      var activeReviewId = ref(null);
      var panelAnswerOpen = ref(false);

      /* ---------------- 主题相关 ---------------- */
      var answersOpen = reactive({});
      var previewImage = ref(null);
      var toasts = ref([]);
      var toastSeq = 0;

      /* ---------------- 设置 ---------------- */
      var settingsForm = reactive({ intervals: [3, 7, 15, 30], backfill_limit: 20, backfill_reset_days: 14 });
      var settingsSaving = ref(false);

      /* ---------------- 导出 ---------------- */
      var exportDialog = reactive({ open: false, busy: false });
      var exportForm = reactive({
        scope: 'folder', folder_id: null, tags: [], with_answer: true, include_tags: false,
      });

      /* ---------------- 题目编辑弹窗 ---------------- */
      var questionEditor = reactive({ open: false, id: null, draftNotice: '' });
      var questionForm = reactive({
        id: null, folder_id: null, stem: '', answer: '', tags: [], is_starred: false,
      });
      var questionSaving = ref(false);
      var questionDraftStatus = ref('');
      var tagDraft = ref('');
      var tagSuggestions = ref([]);

      /* ---------------- 记事本 ---------------- */
      var notesView = ref([]);
      var noteForm = reactive({ id: null, title: '', content: '' });
      var noteSearch = ref('');
      var noteSaving = ref(false);
      var draftStatusText = ref('');
      var draftPrompt = reactive({ show: false, label: '', key: '', payload: null });
      var draftKeysInStorage = ref([]);

      var questionDraftTimer = null;
      var noteDraftTimer = null;
      var noteSearchTimer = null;
      var savedAtTimer = null;
      var lastNoteSavedAt = 0;

      /* ================= 计算属性 ================= */

      var pageTitle = computed(function () { return PAGE_TITLES[currentPage.value] || '错题本'; });

      var reviewCount = computed(function () { return reviewQueue.value.length; });

      var hasStarredDue = computed(function () {
        return reviewQueue.value.some(function (i) { return i.is_starred; });
      });

      var reviewPercent = computed(function () {
        var done = reviewDoneToday.value;
        var total = done + reviewQueue.value.length;
        return total ? Math.round((done / total) * 100) : 0;
      });

      var masteryRate = computed(function () {
        var t = stats.value.total;
        return t ? Math.round((stats.value.mastered / t) * 100) : 0;
      });

      var breadcrumbs = computed(function () {
        var path = [{ label: '首页', go: function () { go('home'); } }];
        if (currentPage.value === 'home') { return [path[0]]; }
        path.push({ label: PAGE_TITLES[currentPage.value] || '' });
        return path;
      });

      var currentFolderName = computed(function () {
        if (!currentFolderId.value) { return ''; }
        var found = '';
        folderTree.value.forEach(function (s) {
          (s.children || []).forEach(function (c) {
            if (c.id === currentFolderId.value) { found = s.name + ' / ' + c.name; }
          });
        });
        return found;
      });

      /** 所有二级大类，带父级路径，供下拉与导出选择。 */
      var categoryOptions = computed(function () {
        var out = [];
        folderTree.value.forEach(function (s) {
          (s.children || []).forEach(function (c) {
            out.push({ id: c.id, name: c.name, path: s.name + ' / ' + c.name });
          });
        });
        return out;
      });

      /** 筛选后的题目列表（按所在文件夹 + filterType + 搜索词）。 */
      var filteredQuestions = computed(function () {
        var kw = tableSearch.value.trim().toLowerCase();
        var folder = currentFolderId.value;
        return questions.value.filter(function (q) {
          // 点击侧边栏二级大类后只显示该文件夹下的题
          if (folder && q.folder_id !== folder) { return false; }
          if (filterType.value === 'starred' && !q.is_starred) { return false; }
          if (filterType.value === 'wrong' && q.mastery_status !== 'still_wrong') { return false; }
          if (filterType.value === 'mastered' && q.mastery_status !== 'mastered') { return false; }
          if (!kw) { return true; }
          var hay = ((q.stem || '') + ' ' + (q.answer || '') + ' ' +
            (q.tags || []).map(function (t) { return t.name; }).join(' ')).toLowerCase();
          return hay.indexOf(kw) !== -1;
        });
      });

      /* ================= Toast ================= */

      function toast(message, type, sticky) {
        toastSeq += 1;
        var id = toastSeq;
        toasts.value.push({ id: id, message: message, type: type || 'success' });
        if (!sticky) {
          window.setTimeout(function () { dismissToast(id); },
            type === 'warning' ? 5000 : 3000);
        }
        return id;
      }

      function dismissToast(id) {
        toasts.value = toasts.value.filter(function (t) { return t.id !== id; });
      }

      /** 统一错误提示：ApiError 的 message 已经是可读文案。 */
      function toastError(err) {
        var msg = err && err.message ? err.message : '操作失败';
        if (err && err.status === 0) { msg = '无法连接后端服务，请确认已启动'; }
        toast(msg, 'error', true);
      }

      /* ================= 路由 ================= */

      function pageFromHash() {
        var raw = (window.location.hash || '').replace(/^#\/?/, '');
        return VALID_PAGES.indexOf(raw) !== -1 ? raw : 'home';
      }

      function go(page) {
        if (VALID_PAGES.indexOf(page) === -1) { page = 'home'; }
        currentPage.value = page;
        drawerOpen.value = false;
        userMenuOpen.value = false;
        if (window.location.hash !== '#/' + page) {
          window.location.hash = '#/' + page;
        }
        loadPage(page);
      }

      function onHashChange() {
        var page = pageFromHash();
        if (page !== currentPage.value) {
          currentPage.value = page;
          loadPage(page);
        }
      }

      /* ================= 载入数据 ================= */

      function loadFolderTree() {
        return API.getFolderTree().then(function (tree) {
          folderTree.value = tree || [];
          tree.forEach(function (s) {
            if (expandedFolders[s.id] === undefined) { expandedFolders[s.id] = true; }
          });
        });
      }

      function loadQuestions() {
        return API.getQuestions().then(function (list) {
          questions.value = list || [];
          recomputeStats();
        });
      }

      function loadTags() {
        return API.getTags().then(function (list) { tags.value = list || []; });
      }

      function loadReview() {
        return API.getReviewToday().then(function (list) {
          reviewQueue.value = list || [];
          if (activeReviewId.value &&
              !reviewQueue.value.some(function (i) { return i.question_id === activeReviewId.value; })) {
            activeReviewId.value = null;
          }
        });
      }

      function loadBackfillStats() {
        return API.getBackfillStats().then(function (s) {
          backfillStats.value = s || backfillStats.value;
        });
      }

      function loadHealth() {
        return API.getHealth().then(function (h) { health.value = h || {}; });
      }

      function loadSettings() {
        return API.getSettings().then(function (s) {
          if (!s) { return; }
          settingsForm.intervals = (s.intervals || [3, 7, 15, 30]).slice();
          settingsForm.backfill_limit = s.backfill_limit;
          settingsForm.backfill_reset_days = s.backfill_reset_days;
        });
      }

      function loadNotes() {
        var p = noteSearch.value.trim()
          ? API.searchNotes(noteSearch.value.trim())
          : API.getNotes();
        return p.then(function (list) { notesView.value = list || []; });
      }

      /** 首页统计：从已有数据推导，避免再造统计接口。 */
      function recomputeStats() {
        var list = questions.value;
        stats.value = {
          total: list.length,
          due: reviewQueue.value.length,
          mastered: list.filter(function (q) { return q.mastery_status === 'mastered'; }).length,
          wrong: list.filter(function (q) { return q.mastery_status === 'still_wrong'; }).length,
        };
      }

      function loadPage(page) {
        loading.value = true;
        loadError.value = '';
        // 侧边栏的学科树是全局导航的一部分，任何页面都该有，
        // 否则切到记事本/设置后左侧会变成"暂无学科"，看起来像数据丢了。
        var jobs = [loadFolderTree()];
        if (page === 'home') {
          jobs.push(loadQuestions(), loadReview(), loadTags(), loadBackfillStats());
        } else if (page === 'questions') {
          jobs.push(loadQuestions(), loadTags());
        } else if (page === 'review') {
          jobs.push(loadReview(), loadBackfillStats());
        } else if (page === 'notes') {
          jobs.push(loadNotes());
        } else if (page === 'stats') {
          jobs.push(loadQuestions(), loadTags(), loadReview(), loadBackfillStats());
        } else if (page === 'settings') {
          jobs.push(loadSettings());
        } else if (page === 'about-data') {
          jobs.push(loadHealth());
          loadDraftKeyList();
        }
        return Promise.all(jobs)
          .then(function () {
            recomputeStats();
            if (page === 'notes') { checkNoteDraft(); }
          })
          .catch(function (err) { loadError.value = err.message || '加载失败'; })
          .then(function () { loading.value = false; });
      }

      function refreshCurrent() { return loadPage(currentPage.value); }

      /* ================= 文件夹树 ================= */

      function toggleFolder(id) { expandedFolders[id] = !expandedFolders[id]; }

      /** 点击二级大类：切换 currentFolderId 并跳到题目页。 */
      function selectFolder(id) {
        currentFolderId.value = id;
        go('questions');
      }

      function promptNewSubject() {
        var name = window.prompt('新建学科名称');
        if (!name || !name.trim()) { return; }
        API.createFolder({ name: name.trim() })
          .then(function () {
            toast('学科「' + name.trim() + '」已创建');
            return loadFolderTree();
          })
          .catch(toastError);
      }

      /* ================= 搜索（单框双查 + 400ms 防抖）================= */

      function onSearchInput() {
        window.clearTimeout(searchDebounce);
        searchDebounce = window.setTimeout(function () {
          go('questions');
          runSearch();
        }, 400);
      }

      /**
       * 单框双查：keyword 与 tag 各发一次，按 id 合并去重。
       * 后端 keyword 不匹配标签，所以必须前端合并（docs/ui-design 3.3）。
       */
      function runSearch() {
        var kw = tableSearch.value.trim();
        if (!kw) { return loadQuestions(); }
        return Promise.all([
          API.getQuestions({ keyword: kw }),
          API.getQuestions({ tag: [kw] }),
        ]).then(function (res) {
          var seen = {};
          var merged = [];
          res[0].concat(res[1]).forEach(function (q) {
            if (!seen[q.id]) { seen[q.id] = true; merged.push(q); }
          });
          questions.value = merged;
          recomputeStats();
        }).catch(toastError);
      }

      /* ================= 题目操作 ================= */

      function starQuestion(q, starred) {
        API.toggleStar(q.id, starred)
          .then(function (updated) { patchQuestion(updated); })
          .catch(toastError);
      }

      function patchQuestion(updated) {
        if (!updated) { return; }
        var i = questions.value.findIndex(function (q) { return q.id === updated.id; });
        if (i !== -1) { questions.value.splice(i, 1, updated); }
        recomputeStats();
      }

      function removeQuestion(q) {
        if (!window.confirm('确定删除这道题吗？（软删除，可在数据库中恢复）')) { return; }
        API.deleteQuestion(q.id)
          .then(function () {
            toast('已删除');
            questions.value = questions.value.filter(function (x) { return x.id !== q.id; });
            recomputeStats();
            return loadFolderTree();
          })
          .catch(toastError);
      }

      /* ================= 打勾 ================= */

      /** 从任意位置打勾：写入记录、从队列移除、刷新计数。 */
      function checkReview(item, mastery) {
        var id = item.question_id || item.id;
        return API.checkReview(id, mastery)
          .then(function () {
            reviewDoneToday.value += 1;
            reviewQueue.value = reviewQueue.value.filter(function (i) {
              return i.question_id !== id;
            });
            if (activeReviewId.value === id) { activeReviewId.value = null; }
            toast('已打勾，下次复习：' + fmtDate(arguments[0] && arguments[0].next_review_at));
            recomputeStats();
            return loadFolderTree();
          })
          .catch(toastError);
      }

      /* ================= 通知面板 ================= */

      function toggleNotif() {
        notifOpen.value = !notifOpen.value;
        userMenuOpen.value = false;
        if (notifOpen.value) {
          loadReview().then(recomputeStats).catch(toastError);
        }
      }

      /** 点击列表项：在面板内展开复习视图（不跳页）。 */
      function expandInPanel(item) {
        if (activeReviewId.value === item.question_id) {
          activeReviewId.value = null;
          return;
        }
        activeReviewId.value = item.question_id;
        panelAnswerOpen.value = false;
      }

      function stepPanelReview(delta) {
        var idx = reviewQueue.value.findIndex(function (i) {
          return i.question_id === activeReviewId.value;
        });
        var next = idx + delta;
        if (next < 0 || next >= reviewQueue.value.length) { return; }
        activeReviewId.value = reviewQueue.value[next].question_id;
        panelAnswerOpen.value = false;
      }

      function checkFromPanel(item) { return checkReview(item); }

      /* ================= 复习页 ================= */

      function toggleAnswer(id) { answersOpen[id] = !answersOpen[id]; }

      function doResetBackfill(spread) {
        backfillBusy.value = true;
        API.resetBackfill(spread)
          .then(function (res) {
            toast('已重置 ' + res.affected_count + ' 道积压题');
            return Promise.all([loadReview(), loadBackfillStats(), loadQuestions()]);
          })
          .catch(toastError)
          .then(function () { backfillBusy.value = false; });
      }

      var backfillBusy = ref(false);

      /* ================= 题目编辑弹窗 + 草稿 ================= */

      function questionDraftKey(id) {
        return id ? DRAFT_KEYS.question(id) : DRAFT_KEYS.questionNew;
      }

      function openQuestionEditor(q) {
        questionEditor.id = q ? q.id : null;
        questionEditor.draftNotice = '';
        questionForm.id = q ? q.id : null;
        questionForm.folder_id = q ? q.folder_id : (currentFolderId.value || null);
        questionForm.stem = q ? (q.stem || '') : '';
        questionForm.answer = q ? (q.answer || '') : '';
        questionForm.tags = q ? (q.tags || []).map(function (t) { return t.name; }) : [];
        questionForm.is_starred = q ? !!q.is_starred : false;
        tagDraft.value = '';
        tagSuggestions.value = [];
        questionEditor.open = true;

        // 进入弹窗时检测草稿，提示恢复（AGENTS.md 4.5）
        var raw = storage.get(questionDraftKey(questionEditor.id));
        if (raw) {
          try {
            var saved = JSON.parse(raw);
            questionEditor.draftNotice =
              '检测到未保存的草稿（' + fmtDate(saved.at) + '）。已为你恢复，可点"忽略草稿"丢弃。';
            questionForm.stem = saved.stem || '';
            questionForm.answer = saved.answer || '';
            questionForm.tags = saved.tags || [];
            questionForm.is_starred = !!saved.is_starred;
            if (saved.folder_id) { questionForm.folder_id = saved.folder_id; }
          } catch (e) { /* 草稿损坏就当没有 */ }
        }
        if (!questions.value.length) { loadQuestions(); }
      }

      function closeQuestionEditor() {
        questionEditor.open = false;
        questionEditor.draftNotice = '';
        questionDraftStatus.value = '';
      }

      function onQuestionEdit() {
        window.clearTimeout(questionDraftTimer);
        questionDraftStatus.value = '正在保存草稿…';
        questionDraftTimer = window.setTimeout(function () {
          var ok = storage.set(questionDraftKey(questionForm.id), JSON.stringify({
            folder_id: questionForm.folder_id,
            stem: questionForm.stem,
            answer: questionForm.answer,
            tags: questionForm.tags,
            is_starred: questionForm.is_starred,
            at: new Date().toISOString(),
          }));
          questionDraftStatus.value = ok ? '草稿已自动保存' : '草稿保存失败（存储不可用）';
          loadDraftKeyList();
        }, DRAFT_DEBOUNCE_MS);
      }

      function discardQuestionDraft() {
        storage.remove(questionDraftKey(questionEditor.id));
        questionEditor.draftNotice = '';
        questionDraftStatus.value = '草稿已丢弃';
        loadDraftKeyList();
      }

      function addTag() {
        var name = tagDraft.value.trim();
        if (!name) { return; }
        if (questionForm.tags.indexOf(name) === -1) { questionForm.tags.push(name); }
        tagDraft.value = '';
        tagSuggestions.value = [];
        onQuestionEdit();
      }

      function pickTag(name) {
        if (questionForm.tags.indexOf(name) === -1) { questionForm.tags.push(name); }
        tagDraft.value = '';
        tagSuggestions.value = [];
        onQuestionEdit();
      }

      function onTagSuggest() {
        var q = tagDraft.value.trim();
        if (!q) { tagSuggestions.value = []; return; }
        API.searchTags(q, 8)
          .then(function (list) {
            tagSuggestions.value = (list || []).filter(function (t) {
              return questionForm.tags.indexOf(t.name) === -1;
            });
          })
          .catch(function () { tagSuggestions.value = []; });
      }

      function saveQuestion(continueAdding) {
        if (!questionForm.folder_id) {
          toast('请先选择所属大类', 'warning', true);
          return;
        }
        questionSaving.value = true;
        var payload = {
          folder_id: questionForm.folder_id,
          stem: questionForm.stem === '' ? null : questionForm.stem,
          answer: questionForm.answer === '' ? null : questionForm.answer,
          tags: questionForm.tags,
          is_starred: questionForm.is_starred,
        };
        var p = questionEditor.id
          ? API.updateQuestion(questionEditor.id, payload)
          : API.createQuestion(payload);

        p.then(function () {
          // 提交成功后清除草稿（AGENTS.md 4.5）
          storage.remove(questionDraftKey(questionEditor.id));
          questionDraftStatus.value = '';
          toast(questionEditor.id ? '已保存' : '题目已创建');
          loadDraftKeyList();
          return Promise.all([loadQuestions(), loadFolderTree()]);
        }).then(function () {
          questionSaving.value = false;
          if (continueAdding) {
            questionForm.stem = '';
            questionForm.answer = '';
            questionForm.tags = [];
            questionEditor.draftNotice = '';
          } else {
            closeQuestionEditor();
          }
        }).catch(function (err) {
          questionSaving.value = false;
          toastError(err);
        });
      }

      /* ================= 记事本 ================= */

      function noteDraftKey(id) {
        return id ? DRAFT_KEYS.note(id) : DRAFT_KEYS.noteNew;
      }

      function newNote() {
        noteForm.id = null;
        noteForm.title = '';
        noteForm.content = '';
        draftStatusText.value = '';
        checkNoteDraft();
      }

      function selectNote(id) {
        if (noteForm.id === id) { return; }
        API.getNote(id)
          .then(function (n) {
            noteForm.id = n.id;
            noteForm.title = n.title || '';
            noteForm.content = n.content || '';
            draftStatusText.value = '';
            checkNoteDraft();
          })
          .catch(toastError);
      }

      /** 输入时防抖 500ms 存 localStorage。 */
      function onNoteEdit() {
        window.clearTimeout(noteDraftTimer);
        noteDraftTimer = window.setTimeout(function () {
          var ok = storage.set(noteDraftKey(noteForm.id), JSON.stringify({
            title: noteForm.title,
            content: noteForm.content,
            at: new Date().toISOString(),
          }));
          if (ok) {
            lastNoteSavedAt = Date.now();
            tickSavedAt();
            loadDraftKeyList();
          } else {
            draftStatusText.value = '草稿保存失败（存储不可用）';
          }
        }, DRAFT_DEBOUNCE_MS);
      }

      /** 右上角"已自动保存 · 刚刚"，随后随时间推移更新措辞。 */
      function tickSavedAt() {
        if (!lastNoteSavedAt) { return; }
        var diff = Date.now() - lastNoteSavedAt;
        var when = '刚刚';
        if (diff >= 60000) { when = Math.floor(diff / 60000) + ' 分钟前'; }
        else if (diff >= 5000) { when = Math.floor(diff / 1000) + ' 秒前'; }
        draftStatusText.value = '已自动保存 · ' + when;
      }

      function checkNoteDraft() {
        var key = noteDraftKey(noteForm.id);
        var raw = storage.get(key);
        draftPrompt.show = false;
        if (!raw || noteForm.id === null && !raw) { return; }
        try {
          var saved = JSON.parse(raw);
          // 与当前内容一致就不必提示
          if (saved.title === noteForm.title && saved.content === noteForm.content) { return; }
          draftPrompt.show = true;
          draftPrompt.key = key;
          draftPrompt.label = fmtDate(saved.at);
          draftPrompt.payload = saved;
        } catch (e) { /* 草稿损坏就当没有 */ }
      }

      function restoreDraft() {
        if (draftPrompt.payload) {
          noteForm.title = draftPrompt.payload.title || '';
          noteForm.content = draftPrompt.payload.content || '';
          lastNoteSavedAt = Date.parse(draftPrompt.payload.at) || Date.now();
          tickSavedAt();
        }
        draftPrompt.show = false;
      }

      function discardDraft() {
        if (draftPrompt.key) { storage.remove(draftPrompt.key); }
        draftPrompt.show = false;
        loadDraftKeyList();
      }

      function onNoteSearchInput() {
        window.clearTimeout(noteSearchTimer);
        noteSearchTimer = window.setTimeout(loadNotes, 300);
      }

      function saveNote() {
        noteSaving.value = true;
        var payload = {
          title: noteForm.title === '' ? null : noteForm.title,
          content: noteForm.content === '' ? null : noteForm.content,
        };
        var p = noteForm.id ? API.updateNote(noteForm.id, payload) : API.createNote(payload);
        p.then(function (saved) {
          noteForm.id = saved.id;
          storage.remove(noteDraftKey(null));      // 新建草稿
          storage.remove(noteDraftKey(saved.id));  // 该笔记旧草稿
          draftStatusText.value = '已保存';
          lastNoteSavedAt = 0;
          toast('笔记已保存');
          loadDraftKeyList();
          return loadNotes();
        }).catch(toastError).then(function () { noteSaving.value = false; });
      }

      function removeNote(id) {
        if (!window.confirm('确定删除这篇笔记吗？')) { return; }
        API.deleteNote(id)
          .then(function () {
            toast('笔记已删除');
            storage.remove(noteDraftKey(id));
            if (noteForm.id === id) { newNote(); }
            return loadNotes();
          })
          .catch(toastError);
      }

      /* ================= 草稿清单（数据说明页）================= */

      function loadDraftKeyList() { draftKeysInStorage.value = storage.keys(); }

      function clearDraftKey(key) {
        storage.remove(key);
        loadDraftKeyList();
        toast('已清除草稿 ' + key);
      }

      /* ================= 设置 ================= */

      function saveSettings() {
        settingsSaving.value = true;
        API.updateSettings({
          intervals: settingsForm.intervals,
          backfill_limit: settingsForm.backfill_limit,
          backfill_reset_days: settingsForm.backfill_reset_days,
        }).then(function (s) {
          settingsForm.intervals = (s.intervals || []).slice();
          settingsForm.backfill_limit = s.backfill_limit;
          settingsForm.backfill_reset_days = s.backfill_reset_days;
          toast('设置已保存');
        }).catch(toastError).then(function () { settingsSaving.value = false; });
      }

      /** 三项都传 null：后端语义为"删除配置项、回退默认值"（requirements 4.8）。 */
      function resetSettingsToDefault() {
        settingsSaving.value = true;
        API.updateSettings({ intervals: null, backfill_limit: null, backfill_reset_days: null })
          .then(function (s) {
            settingsForm.intervals = (s.intervals || []).slice();
            settingsForm.backfill_limit = s.backfill_limit;
            settingsForm.backfill_reset_days = s.backfill_reset_days;
            toast('已恢复默认值');
          })
          .catch(toastError).then(function () { settingsSaving.value = false; });
      }

      /* ================= 导出 ================= */

      function openExport() {
        exportForm.folder_id = currentFolderId.value || null;
        exportDialog.open = true;
        if (!tags.value.length) { loadTags(); }
      }

      function doExport() {
        exportDialog.busy = true;
        var payload = {
          scope: exportForm.scope,
          with_answer: exportForm.with_answer,
          include_tags: exportForm.include_tags,
        };
        if (exportForm.scope === 'folder' && exportForm.folder_id) {
          payload.folder_id = exportForm.folder_id;
        }
        if (exportForm.scope === 'tags') {
          payload.tags = exportForm.tags;
        }
        if (exportForm.scope === 'manual') {
          payload.question_ids = questions.value.map(function (q) { return q.id; });
        }
        API.exportPDF(payload)
          .then(function (res) {
            var url = window.URL.createObjectURL(res.blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = res.filename || 'cuotiben.pdf';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            exportDialog.open = false;
            toast('已开始下载 PDF');
          })
          .catch(toastError)
          .then(function () { exportDialog.busy = false; });
      }

      /* ================= 统计页辅助 ================= */

      function distPercent(count) {
        var max = 1;
        folderTree.value.forEach(function (s) {
          max = Math.max(max, s.question_count || 0);
        });
        return Math.round(((count || 0) / max) * 100);
      }

      function cloudSize(count) {
        var max = 1;
        tags.value.forEach(function (t) { max = Math.max(max, t.question_count || 0); });
        return 12 + Math.round(((count || 0) / max) * 10);
      }

      /* ================= 生命周期 ================= */

      var pollTimer = null;
      var savedAtTicker = null;

      onMounted(function () {
        window.addEventListener('hashchange', onHashChange);
        loadPage(currentPage.value);
        // 通知每 60s 轮询（requirements 2.9 要求 30~60s）
        pollTimer = window.setInterval(function () {
          loadReview().then(recomputeStats).catch(function () { /* 静默 */ });
        }, 60000);
        savedAtTicker = window.setInterval(tickSavedAt, 5000);
        loadDraftKeyList();
      });

      onUnmounted(function () {
        window.removeEventListener('hashchange', onHashChange);
        window.clearInterval(pollTimer);
        window.clearInterval(savedAtTicker);
      });

      return {
        // 状态
        currentPage: currentPage,
        loading: loading,
        loadError: loadError,
        sidebarCollapsed: sidebarCollapsed,
        drawerOpen: drawerOpen,
        navItems: NAV_ITEMS,
        filterOptions: filterOptions,
        masteryChoices: MASTERY_CHOICES,
        // 数据
        folderTree: folderTree,
        questions: questions,
        filteredQuestions: filteredQuestions,
        tags: tags,
        reviewQueue: reviewQueue,
        reviewCount: reviewCount,
        hasStarredDue: hasStarredDue,
        stats: stats,
        backfillStats: backfillStats,
        health: health,
        reviewDoneToday: reviewDoneToday,
        reviewPercent: reviewPercent,
        masteryRate: masteryRate,
        // 路由派生
        pageTitle: pageTitle,
        breadcrumbs: breadcrumbs,
        // 筛选
        currentFolderId: currentFolderId,
        currentFolderName: currentFolderName,
        filterType: filterType,
        tableSearch: tableSearch,
        expandedFolders: expandedFolders,
        // 通知面板
        notifOpen: notifOpen,
        userMenuOpen: userMenuOpen,
        activeReviewId: activeReviewId,
        panelAnswerOpen: panelAnswerOpen,
        answersOpen: answersOpen,
        previewImage: previewImage,
        toasts: toasts,
        // 题目弹窗
        questionEditor: questionEditor,
        questionForm: questionForm,
        questionSaving: questionSaving,
        questionDraftStatus: questionDraftStatus,
        categoryOptions: categoryOptions,
        tagDraft: tagDraft,
        tagSuggestions: tagSuggestions,
        // 记事本
        notesView: notesView,
        noteForm: noteForm,
        noteSearch: noteSearch,
        noteSaving: noteSaving,
        draftStatusText: draftStatusText,
        draftPrompt: draftPrompt,
        draftKeysInStorage: draftKeysInStorage,
        // 设置
        settingsForm: settingsForm,
        settingsSaving: settingsSaving,
        // 导出
        exportDialog: exportDialog,
        exportForm: exportForm,
        // 方法
        go: go,
        refreshCurrent: refreshCurrent,
        toggleSidebar: function () { sidebarCollapsed.value = !sidebarCollapsed.value; },
        toggleFolder: toggleFolder,
        selectFolder: selectFolder,
        promptNewSubject: promptNewSubject,
        onSearchInput: onSearchInput,
        starQuestion: starQuestion,
        removeQuestion: removeQuestion,
        checkReview: checkReview,
        toggleNotif: toggleNotif,
        expandInPanel: expandInPanel,
        stepPanelReview: stepPanelReview,
        checkFromPanel: checkFromPanel,
        toggleAnswer: toggleAnswer,
        doResetBackfill: doResetBackfill,
        openQuestionEditor: openQuestionEditor,
        closeQuestionEditor: closeQuestionEditor,
        onQuestionEdit: onQuestionEdit,
        discardQuestionDraft: discardQuestionDraft,
        addTag: addTag,
        pickTag: pickTag,
        onTagSuggest: onTagSuggest,
        saveQuestion: saveQuestion,
        newNote: newNote,
        selectNote: selectNote,
        onNoteEdit: onNoteEdit,
        onNoteSearchInput: onNoteSearchInput,
        saveNote: saveNote,
        removeNote: removeNote,
        restoreDraft: restoreDraft,
        discardDraft: discardDraft,
        clearDraftKey: clearDraftKey,
        saveSettings: saveSettings,
        resetSettingsToDefault: resetSettingsToDefault,
        openExport: openExport,
        doExport: doExport,
        dismissToast: dismissToast,
        fmtDate: fmtDate,
        distPercent: distPercent,
        cloudSize: cloudSize,
        masteryLabel: function (s) { return MASTERY_LABEL[s] || s; },
      };
    },
  };

  var app = createApp(App);

  // 模板里的错误不要静默：抛出来并提示，否则页面会莫名其妙地不更新
  app.config.errorHandler = function (err, instance, info) {
    // eslint-disable-next-line no-console
    console.error('[错题本] 渲染错误:', info, err);
  };

  // 等 DOM ready 再挂载（脚本在 body 末尾，通常已就绪）
  function mount() {
    if (!document.getElementById('app')) { return; }
    app.mount('#app');
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', mount);
  } else {
    mount();
  }
})();
