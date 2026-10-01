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

  /** 草稿恢复提示条自动收起时间（ui-design 6.2：5s 后自动收起）。 */
  var DRAFT_PROMPT_MS = 5000;

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
      var isMobile = ref(false);

      /**
       * 用户是否手动折叠过侧边栏。
       * 手动选择优先于断点默认值（ui-design 7：平板端"默认"折叠为 64px），
       * 否则用户在平板上点开又会立刻被 resize 折回去。
       */
      var sidebarUserSet = ref(false);

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
      /** 复习评价按下的按钮（用于"点击缩放 + 背景填充"的即时反馈）。 */
      var ratingPicked = ref(null);
      /** 刚打勾的题目 id（用于行背景短暂高亮）。 */
      var flashedQuestionId = ref(null);
      /** 屏幕阅读器播报文案（aria-live，ui-design 第 8 节）。 */
      var liveMessage = ref('');

      /* ---------------- 设置 ---------------- */
      var settingsForm = reactive({ intervals: [3, 7, 15, 30], backfill_limit: 20, backfill_reset_days: 14 });
      var settingsSaving = ref(false);

      /* ---------------- 导出 ---------------- */
      var exportDialog = reactive({ open: false, busy: false });
      /** 导出弹窗里的错误文案（如"该范围内没有题目"），比 toast 更易看到。 */
      var exportError = ref('');
      /** 手动勾选的题目 id（供导出 scope=manual 使用）。 */
      var selectedQuestionIds = ref([]);
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
      /**
       * 联想下拉是否打开。
       *
       * 不用 computed(tagSuggestions.length > 0)：Esc/Tab 关闭后还要能保留
       * 输入内容，而 v-model 的 @input 又会重新触发搜索把下拉顶开 ——
       * 需要一个独立开关来记住"用户主动关掉了"。
       */
      var tagSuggestOpen = ref(false);
      /** 键盘高亮项索引；-1 表示没有高亮（此时回车按"新建标签"处理）。 */
      var tagHighlight = ref(-1);
      /** 是否用键盘移动过高亮（只影响 Enter 的语义，见 onTagKeydown）。 */
      var tagKeyNavUsed = ref(false);

      /* ---------------- 记事本 ---------------- */
      var notesView = ref([]);
      var noteForm = reactive({ id: null, title: '', content: '' });
      var noteSearch = ref('');
      var noteSaving = ref(false);
      var draftStatusText = ref('');
      /** 草稿是否正在写入（用于「保存期间显示加载态」）。 */
      var draftSaving = ref(false);
      var draftPrompt = reactive({
        show: false, label: '', key: '', payload: null, leaving: false,
      });
      var draftPromptTimer = null;
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

      /* ================= 响应式（ui-design 第 7 节）================= */

      var MOBILE_MAX = 767;      // < 768 移动端：抽屉
      var TABLET_MAX = 1200;     // 768~1200 平板端：默认折叠 64px

      function viewportWidth() {
        return window.innerWidth
          || document.documentElement.clientWidth
          || 1280;
      }

      /**
       * 按断点应用侧边栏默认宽度。
       *
       * 这三个断点的差异**必须用 JS 判断**，不能只靠 CSS 媒体查询：
       *   - 平板端要"默认折叠"，但用户点开后应当能展开 —— 纯 CSS 无法表达
       *     "默认值 + 用户覆盖"这种状态。
       *   - 焦点管理、抽屉开合、键盘监听也都要知道当前断点。
       * CSS 侧只负责「折叠态长什么样」，由 .is-collapsed 类驱动。
       */
      function applyBreakpoint() {
        var w = viewportWidth();
        isMobile.value = w <= MOBILE_MAX;
        if (isMobile.value) {
          // 移动端用抽屉，折叠与否无意义
          drawerOpen.value = false;
          return;
        }
        if (!sidebarUserSet.value) {
          sidebarCollapsed.value = w <= TABLET_MAX;
        }
      }

      /** 顶部的折叠按钮：记为用户的手动选择，之后不再被断点覆盖。 */
      function toggleSidebar() {
        sidebarCollapsed.value = !sidebarCollapsed.value;
        sidebarUserSet.value = true;
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

      /**
       * 题目列表分页迁移阈值（AGENTS.md 3.2）。
       *
       * 当前是**客户端过滤**：把全量题目拉到前端再筛。
       * 到 500 题这个规模还能接受，再多就该改成后端分页查询了。
       * 这里只做计数监控 —— 超阈值时在控制台警告，提醒该迁移了；
       * 不自动改变行为（自动切模式会让"筛选结果不对"这类问题很难归因）。
       */
      var PAGINATION_HINT_THRESHOLD = 500;
      /** 同一会话里只警告一次，避免每次刷新都刷屏。 */
      var paginationWarned = false;

      function checkPaginationThreshold(count) {
        if (count <= PAGINATION_HINT_THRESHOLD || paginationWarned) { return; }
        paginationWarned = true;
        console.warn(
          '[错题本] 题目数已达 ' + count + ' 题，超过客户端过滤阈值 '
          + PAGINATION_HINT_THRESHOLD + '。\n'
          + '当前实现把全量题目拉到前端过滤（见 AGENTS.md 3.2），'
          + '题目继续增长会影响加载与筛选性能。\n'
          + '迁移方式：题目列表改为后端分页查询（GET /questions 增加 '
          + 'page/page_size），前端只请求当前页，不再全量拉取。'
        );
      }

      function loadQuestions() {
        return API.getQuestions().then(function (list) {
          questions.value = list || [];
          checkPaginationThreshold(questions.value.length);
          recomputeStats();
        });
      }

      function loadTags() {
        return API.getTags().then(function (list) { tags.value = list || []; });
      }

      function loadReview() {
        return API.getReviewToday().then(function (list) {
          var before = reviewQueue.value.length;
          reviewQueue.value = list || [];
          if (activeReviewId.value &&
              !reviewQueue.value.some(function (i) { return i.question_id === activeReviewId.value; })) {
            activeReviewId.value = null;
          }
          // 队列数量变化时播报（ui-design 8：状态变更用 aria-live）
          if (reviewQueue.value.length !== before) {
            announce('今日待复习 ' + reviewQueue.value.length + ' 题');
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
          checkPaginationThreshold(questions.value.length);
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

      /**
       * 从任意位置打勾：写入记录、从队列移除、刷新计数。
       *
       * 传入 opts.flashId 时，打完给那一行加一个短暂高亮
       * （ui-design 6.3「打勾即时反馈：对勾绘制动画，行背景短暂高亮」）。
       */
      function checkReview(item, mastery, opts) {
        var id = item.question_id || item.id;
        var options = opts || {};
        return API.checkReview(id, mastery)
          .then(function (record) {
            reviewDoneToday.value += 1;
            reviewQueue.value = reviewQueue.value.filter(function (i) {
              return i.question_id !== id;
            });
            if (activeReviewId.value === id) { activeReviewId.value = null; }
            // 注意：这里曾经写成 arguments[0]，但那是**回调自己**的 arguments，
            // 结果 next_review_at 永远是 undefined、Toast 显示 "—"。
            var next = record && record.next_review_at ? fmtDate(record.next_review_at) : '—';
            toast('已打勾，下次复习：' + next);
            announce('已打勾，下次复习 ' + next);
            recomputeStats();
            if (options.flashId) { flashRow(options.flashId); }
            return loadFolderTree();
          })
          .catch(toastError);
      }

      /** 行背景短暂高亮，动画结束后自动摘掉 class。 */
      function flashRow(id) {
        flashedQuestionId.value = id;
        window.setTimeout(function () {
          if (flashedQuestionId.value === id) { flashedQuestionId.value = null; }
        }, 800);
      }

      /**
       * 复习页四档评价（ui-design 6.3）：点击后按钮缩放 + 背景填充，
       * 200ms 后自动切到下一题。
       */
      function rateReview(item, mastery) {
        var key = item.question_id + ':' + mastery;
        ratingPicked.value = key;
        window.setTimeout(function () {
          ratingPicked.value = null;
          checkReview(item, mastery);
        }, 200);
      }

      /* ================= 通知面板 ================= */

      function toggleNotif() {
        var wasOpen = notifOpen.value;
        notifOpen.value = !notifOpen.value;
        userMenuOpen.value = false;
        if (notifOpen.value) {
          rememberFocus();
          loadReview().then(recomputeStats).catch(toastError);
          focusFirstMenuItem('notif');
        } else if (wasOpen) {
          restoreFocus();
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

      /* ================= 可访问性：焦点、Esc、点击外部（ui-design 第 8 节）================= */

      var FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), '
        + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

      /** 打开浮层前的焦点位置，关闭后归还（ui-design 8：焦点管理）。 */
      var lastFocused = null;

      /** 当前打开的弹窗元素；null 表示没有弹窗。 */
      function openModalEl() {
        return document.querySelector('.modal-mask .modal');
      }

      function modalIsOpen() {
        return !!(questionEditor.open || exportDialog.open || previewImage.value);
      }

      function anyOverlayOpen() {
        return modalIsOpen() || notifOpen.value || userMenuOpen.value;
      }

      /** 关闭所有浮层。Esc 与点击外部都走这里，保证行为一致。 */
      function closeOverlays() {
        if (questionEditor.open) { closeQuestionEditor(); return; }
        if (exportDialog.open) { exportDialog.open = false; restoreFocus(); return; }
        if (previewImage.value) { previewImage.value = null; restoreFocus(); return; }
        if (notifOpen.value) { closeNotif(); return; }
        if (userMenuOpen.value) { closeUserMenu(); }
      }

      /**
       * 弹窗打开时把焦点移入，并在弹窗内循环（focus trap）。
       * 不这么做的话，Tab 会跑到弹窗背后的侧边栏与顶部栏上 ——
       * 视觉上焦点"消失"在遮罩后面，键盘用户无法操作弹窗。
       */
      function trapFocus(event) {
        var modal = openModalEl();
        if (!modal) { return; }
        var items = Array.prototype.filter.call(
          modal.querySelectorAll(FOCUSABLE),
          function (el) { return el.offsetParent !== null || el === document.activeElement; }
        );
        if (!items.length) { return; }
        var first = items[0];
        var last = items[items.length - 1];
        var active = document.activeElement;

        if (event.shiftKey) {
          if (active === first || !modal.contains(active)) {
            event.preventDefault();
            last.focus();
          }
        } else if (active === last || !modal.contains(active)) {
          event.preventDefault();
          first.focus();
        }
      }

      /** 弹窗打开时把焦点移进去（优先第一个可聚焦元素）。 */
      function focusIntoModal() {
        nextTick(function () {
          var modal = openModalEl();
          if (!modal) { return; }
          var items = Array.prototype.filter.call(
            modal.querySelectorAll(FOCUSABLE),
            function (el) { return el.offsetParent !== null; }
          );
          if (items.length) { items[0].focus(); } else { modal.focus(); }
        });
      }

      /** 关闭浮层后把焦点还给触发元素。 */
      function restoreFocus() {
        if (lastFocused && document.contains(lastFocused)) {
          lastFocused.focus();
        }
        lastFocused = null;
      }

      /** 记录浮层打开前的焦点（在各 open 函数里调用）。 */
      function rememberFocus() {
        lastFocused = document.activeElement;
      }

      /** 严格模式下给 aria-live 区域播报一句话。 */
      function announce(text) {
        if (!text) { return; }
        // 清空再赋值：内容相同的两次播报读屏不会重复朗读
        liveMessage.value = '';
        nextTick(function () { liveMessage.value = text; });
      }

      /**
       * 草稿恢复提示出现时也播报一次（ui-design 8：状态变更用 aria-live）。
       * 提示条本身有 role="status"，但它常常在页面加载瞬间就出现，
       * 读屏可能错过，所以再走一遍统一的播报区。
       */
      watch(function () { return draftPrompt.show; }, function (show) {
        if (show) { announce('检测到未提交的草稿，可选择恢复或丢弃'); }
      });

      /**
       * 全局键盘：Esc 关闭浮层、Tab 在弹窗内循环、方向键切题。
       * 只在有浮层时不劫持方向键以外的按键。
       */
      function onGlobalKeydown(event) {
        var tag = (event.target && event.target.tagName) || '';
        var typing = tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT'
          || (event.target && event.target.isContentEditable);

        // Esc：关弹窗 / 关下拉（输入框里也允许，符合"Esc 关闭弹窗"的预期）
        if (event.key === 'Escape' && anyOverlayOpen()) {
          event.preventDefault();
          closeOverlays();
          restoreFocus();
          return;
        }

        // Tab：弹窗打开时把焦点锁在弹窗内
        if (event.key === 'Tab' && modalIsOpen()) {
          trapFocus(event);
          return;
        }

        if (typing || event.altKey || event.ctrlKey || event.metaKey) { return; }

        // 通知面板展开中：←/→ 切上一题/下一题
        if (notifOpen.value && activeReviewId.value !== null) {
          if (event.key === 'ArrowLeft') {
            event.preventDefault();
            stepPanelReview(-1);
            return;
          }
          if (event.key === 'ArrowRight') {
            event.preventDefault();
            stepPanelReview(1);
            return;
          }
        }

        // 复习页：←/→ 在复习卡片之间滚动
        if (currentPage.value === 'review' && reviewQueue.value.length) {
          if (event.key === 'ArrowLeft' || event.key === 'ArrowRight') {
            event.preventDefault();
            stepReviewCard(event.key === 'ArrowRight' ? 1 : -1);
          }
        }
      }

      /** 点击浮层外部时关闭（通知面板、用户菜单）。 */
      function onDocumentClick(event) {
        if (!notifOpen.value && !userMenuOpen.value) { return; }
        var target = event.target;
        if (target && target.closest
            && (target.closest('.notif') || target.closest('.dropdown'))) {
          return;   // 点在面板/菜单自身或其触发按钮上，交给各自的 @click 处理
        }
        // 走 closeNotif/closeUserMenu 而不是直接改标志位：
        // 这样右键、中键点击外部关闭时也会把焦点还给触发按钮。
        if (notifOpen.value) { closeNotif(); }
        if (userMenuOpen.value) { closeUserMenu(); }
      }

      /* ---------------- 下拉/面板的键盘导航（ui-design 8）--------------- */

      /** 菜单里可聚焦的项：显式 role=menuitem 的优先，其次通用可聚焦元素。 */
      var EXPLICIT_MENU_ITEMS = '[role="menuitem"]';
      var GENERIC_FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), '
        + 'select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

      /** 当前打开的菜单容器（通知面板或用户菜单）。 */
      function activeMenuEl() {
        if (notifOpen.value) { return document.getElementById('notif-panel'); }
        if (userMenuOpen.value) { return document.getElementById('user-menu'); }
        return null;
      }

      /**
       * 菜单项列表。
       *
       * 通知面板里**只有标题行**是菜单项：面板展开复习视图后，里面还有
       * 打勾/上一题/下一题等按钮，若把它们也算进 ↑/↓ 的移动范围，
       * 方向键就会在按钮之间乱跳，与"↑/↓ 在菜单项之间移动"的预期不符。
       * 那些按钮本身是原生 button，Tab 可达、Enter/Space 可触发，无需额外处理。
       */
      function menuItems(container) {
        if (!container) { return []; }
        var explicit = Array.prototype.filter.call(
          container.querySelectorAll(EXPLICIT_MENU_ITEMS),
          function (el) { return !el.disabled; });
        if (explicit.length) { return explicit; }
        return Array.prototype.filter.call(
          container.querySelectorAll(GENERIC_FOCUSABLE),
          function (el) { return !el.disabled; });
      }

      /**
       * 菜单键盘：↑/↓/Home/End 移动焦点，Esc 关闭并把焦点还给触发按钮。
       *
       * 返回 false 表示按键没被处理（例如 review_queue 为空时，
       * 方向键应当去处理"复习页卡片滚动"这个别的语义），由全局处理器接手。
       */
      function onMenuKeydown(event, kind) {
        var container = document.getElementById(
          kind === 'notif' ? 'notif-panel' : 'user-menu');
        if (!container) { return false; }
        var items = menuItems(container);
        if (!items.length) { return false; }

        var key = event.key;
        if (key === 'Escape') {
          event.preventDefault();
          event.stopPropagation();
          if (kind === 'notif') { closeNotif(); } else { closeUserMenu(); }
          return true;
        }
        if (key !== 'ArrowDown' && key !== 'ArrowUp'
            && key !== 'Home' && key !== 'End') {
          // Enter/Space 交给元素自身（原生 button 会触发 click）
          return false;
        }

        event.preventDefault();
        event.stopPropagation();
        var current = items.indexOf(document.activeElement);
        var next;
        if (key === 'Home') {
          next = 0;
        } else if (key === 'End') {
          next = items.length - 1;
        } else if (current === -1) {
          next = key === 'ArrowDown' ? 0 : items.length - 1;
        } else {
          next = current + (key === 'ArrowDown' ? 1 : -1);
          // 循环：到底再按回到另一端，符合菜单的常见预期
          if (next < 0) { next = items.length - 1; }
          if (next >= items.length) { next = 0; }
        }
        items[next].focus();
        return true;
      }

      /** 打开菜单后把焦点放进第一项（否则 ↑/↓ 没有起点）。 */
      function focusFirstMenuItem(kind) {
        nextTick(function () {
          var container = document.getElementById(
            kind === 'notif' ? 'notif-panel' : 'user-menu');
          var items = menuItems(container);
          if (items.length) { items[0].focus(); }
        });
      }

      function closeNotif() {
        notifOpen.value = false;
        restoreFocus();
      }

      function closeUserMenu() {
        userMenuOpen.value = false;
        restoreFocus();
      }

      function toggleUserMenu() {
        var wasOpen = userMenuOpen.value;
        userMenuOpen.value = !userMenuOpen.value;
        notifOpen.value = false;
        if (userMenuOpen.value) {
          rememberFocus();
          focusFirstMenuItem('user');
        } else if (wasOpen) {
          restoreFocus();
        }
      }

      /* ---------------- 弹窗打开时锁背景滚动（ui-design 8）--------------- */

      function updateScrollLock(locked) {
        document.body.style.overflow = locked ? 'hidden' : '';
      }

      // 三类弹窗任一打开即锁滚动。用 watch 而不是散落在各 open/close 里，
      // 避免将来新增弹窗时漏掉其中一条路径（Esc / 遮罩 / 按钮都要还原）。
      watch(modalIsOpen, updateScrollLock);

      /** 复习页按 ←/→ 滚动到上/下一张卡片。 */
      function stepReviewCard(delta) {
        var cards = document.querySelectorAll('.review-card');
        if (!cards.length) { return; }
        var cardsArr = Array.prototype.slice.call(cards);
        var current = cardsArr.findIndex(function (el) {
          var r = el.getBoundingClientRect();
          return r.top >= -8;
        });
        if (current === -1) { current = 0; }
        var next = Math.min(Math.max(current + delta, 0), cardsArr.length - 1);
        cardsArr[next].scrollIntoView({ behavior: 'smooth', block: 'start' });
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
        rememberFocus();
        questionEditor.open = true;
        focusIntoModal();

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
        restoreFocus();
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
        tagSuggestOpen.value = false;
        tagHighlight.value = -1;
        onQuestionEdit();
      }

      function pickTag(name) {
        if (questionForm.tags.indexOf(name) === -1) { questionForm.tags.push(name); }
        tagDraft.value = '';
        tagSuggestions.value = [];
        tagSuggestOpen.value = false;
        tagHighlight.value = -1;
        tagKeyNavUsed.value = false;
        onQuestionEdit();
      }

      function onTagSuggest() {
        var q = tagDraft.value.trim();
        tagHighlight.value = -1;
        tagKeyNavUsed.value = false;
        if (!q) { tagSuggestions.value = []; tagSuggestOpen.value = false; return; }
        API.searchTags(q, 8)
          .then(function (list) {
            tagSuggestions.value = (list || []).filter(function (t) {
              return questionForm.tags.indexOf(t.name) === -1;
            });
            tagSuggestOpen.value = tagSuggestions.value.length > 0;
          })
          .catch(function () {
            tagSuggestions.value = [];
            tagSuggestOpen.value = false;
          });
      }

      /** 关闭联想下拉，但**保留输入内容**（Esc / Tab 的语义）。 */
      function closeTagSuggest() {
        tagSuggestOpen.value = false;
        tagHighlight.value = -1;
        tagKeyNavUsed.value = false;
      }

      /** 清空联想状态（选中之后）。 */
      function resetTagSuggest() {
        tagSuggestions.value = [];
        tagSuggestOpen.value = false;
        tagHighlight.value = -1;
        tagKeyNavUsed.value = false;
      }

      /**
       * 标签输入框的键盘处理（ui-design 8 的键盘可达性）。
       *
       * 语义说明（**两种 Enter 行为不同，是刻意的**）：
       *   - ↑/↓ 选中某项后按 Enter：把该标签**填入输入框**并关闭下拉，
       *     再由用户回车确认（此时走 addTag）—— 这是自动补全的常见做法，
       *     让用户先看到填进去的内容再决定。
       *   - 没有高亮时按 Enter：沿用原行为，**直接创建**输入框里的标签。
       * 若把"选中建议"也做成直接成标签，用户就没有机会改这个词了。
       */
      function onTagKeydown(event) {
        var key = event.key;
        var wasOpen = tagSuggestOpen.value;

        if (key === 'Escape') {
          // 下拉没开时不拦 Esc：应当去关闭外层弹窗
          if (!wasOpen) { return false; }
          event.preventDefault();
          event.stopPropagation();
          closeTagSuggest();
          return true;
        }

        if (key === 'Tab') {
          // Tab 关闭下拉但保留输入，然后让 Tab 继续做焦点移动
          if (wasOpen) { closeTagSuggest(); }
          return false;
        }

        if (key === 'ArrowDown' || key === 'ArrowUp') {
          if (!wasOpen) { return false; }
          event.preventDefault();
          event.stopPropagation();
          var total = tagSuggestions.value.length;
          if (!total) { return true; }
          var step = key === 'ArrowDown' ? 1 : -1;
          var next = tagHighlight.value + step;
          if (tagHighlight.value === -1) {
            // 从未高亮起步：↓ 落到第一项，↑ 落到最后一项
            next = key === 'ArrowDown' ? 0 : total - 1;
          } else if (next < 0) {
            next = total - 1;          // 边界循环
          } else if (next >= total) {
            next = 0;
          }
          tagHighlight.value = next;
          tagKeyNavUsed.value = true;
          return true;
        }

        if (key === 'Enter') {
          if (wasOpen && tagKeyNavUsed.value && tagHighlight.value >= 0) {
            event.preventDefault();
            event.stopPropagation();
            var picked = tagSuggestions.value[tagHighlight.value];
            if (picked) {
              tagDraft.value = picked.name;   // 只填入，不直接成标签
              closeTagSuggest();
            }
            return true;
          }
          if (wasOpen && tagHighlight.value >= 0) {
            // 鼠标悬停高亮但没用键盘：按自动补全惯例取该项
            event.preventDefault();
            event.stopPropagation();
            var hovered = tagSuggestions.value[tagHighlight.value];
            if (hovered) {
              tagDraft.value = hovered.name;
              closeTagSuggest();
            }
            return true;
          }
          // 无高亮：保持原语义 —— 直接创建输入框里的标签
          event.preventDefault();
          addTag();
          return true;
        }

        return false;
      }

      /** 鼠标悬停时同步高亮（与键盘共用同一份高亮状态）。 */
      function hoverTagSuggest(index) {
        tagHighlight.value = index;
        tagKeyNavUsed.value = false;
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
        // 「保存期间显示加载态」（ui-design 6.2）：按下输入即进入保存中，
        // 防抖窗口结束、真正写完之后再切回「已自动保存」。
        draftSaving.value = true;
        draftStatusText.value = '正在保存草稿…';
        noteDraftTimer = window.setTimeout(function () {
          var ok = storage.set(noteDraftKey(noteForm.id), JSON.stringify({
            title: noteForm.title,
            content: noteForm.content,
            at: new Date().toISOString(),
          }));
          draftSaving.value = false;
          if (ok) {
            lastNoteSavedAt = Date.now();
            tickSavedAt();
            loadDraftKeyList();
            // 走统一的 aria-live 播报区（见 announce 的注释）
            announce('笔记草稿已自动保存');
          } else {
            draftStatusText.value = '草稿保存失败（存储不可用）';
            announce('草稿保存失败，存储不可用');
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
        window.clearTimeout(draftPromptTimer);
        if (!raw || noteForm.id === null && !raw) { return; }
        try {
          var saved = JSON.parse(raw);
          // 与当前内容一致就不必提示
          if (saved.title === noteForm.title && saved.content === noteForm.content) { return; }
          draftPrompt.show = true;
          draftPrompt.key = key;
          draftPrompt.label = fmtDate(saved.at);
          draftPrompt.payload = saved;
          // 顶部滑入提示条，5s 后自动收起（ui-design 6.2）
          draftPromptTimer = window.setTimeout(function () {
            if (draftPrompt.show) {
              draftPrompt.leaving = true;
              window.setTimeout(function () {
                draftPrompt.show = false;
                draftPrompt.leaving = false;
              }, 200);
            }
          }, DRAFT_PROMPT_MS);
        } catch (e) { /* 草稿损坏就当没有 */ }
      }

      /** 用户手动点了「恢复」或「忽略」时，取消自动收起倒计时。 */
      function closeDraftPrompt() {
        window.clearTimeout(draftPromptTimer);
        draftPrompt.show = false;
        draftPrompt.leaving = false;
      }

      function restoreDraft() {
        if (draftPrompt.payload) {
          noteForm.title = draftPrompt.payload.title || '';
          noteForm.content = draftPrompt.payload.content || '';
          lastNoteSavedAt = Date.parse(draftPrompt.payload.at) || Date.now();
          tickSavedAt();
        }
        closeDraftPrompt();
      }

      function discardDraft() {
        if (draftPrompt.key) { storage.remove(draftPrompt.key); }
        closeDraftPrompt();
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
        exportError.value = '';
        // 有勾选时默认走"手动勾选"，否则按当前文件夹
        if (selectedQuestionIds.value.length) {
          exportForm.scope = 'manual';
        }
        rememberFocus();
        exportDialog.open = true;
        focusIntoModal();
        if (!tags.value.length) { loadTags(); }
      }

      /** 勾选/取消单题。 */
      function toggleSelectQuestion(id, checked) {
        var list = selectedQuestionIds.value.slice();
        var at = list.indexOf(id);
        if (checked && at === -1) { list.push(id); }
        if (!checked && at !== -1) { list.splice(at, 1); }
        selectedQuestionIds.value = list;
      }

      /** 当前列表是否已全选（用于表头复选框的 checked 态）。 */
      var allFilteredSelected = computed(function () {
        var rows = filteredQuestions.value;
        if (!rows.length) { return false; }
        return rows.every(function (q) {
          return selectedQuestionIds.value.indexOf(q.id) !== -1;
        });
      });

      /** 表头全选/取消全选。 */
      function toggleSelectAll(checked) {
        var ids = filteredQuestions.value.map(function (q) { return q.id; });
        if (!checked) {
          selectedQuestionIds.value = selectedQuestionIds.value.filter(function (id) {
            return ids.indexOf(id) === -1;
          });
          return;
        }
        var merged = selectedQuestionIds.value.slice();
        ids.forEach(function (id) { if (merged.indexOf(id) === -1) { merged.push(id); } });
        selectedQuestionIds.value = merged;
      }

      function doExport() {
        exportDialog.busy = true;
        exportError.value = '';
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
          // 只导出**用户勾选**的题；此前这里错误地发送了当前列表的全部题目
          payload.question_ids = selectedQuestionIds.value.slice();
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
          .catch(function (err) {
            // 422（该范围内没有题目 / 缺必填参数）在弹窗里显示，别只弹 toast
            exportError.value = err && err.message ? err.message : '导出失败';
            toastError(err);
          })
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
        window.addEventListener('keydown', onGlobalKeydown);
        document.addEventListener('click', onDocumentClick);
        window.addEventListener('resize', applyBreakpoint);
        applyBreakpoint();
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
        window.removeEventListener('keydown', onGlobalKeydown);
        document.removeEventListener('click', onDocumentClick);
        window.removeEventListener('resize', applyBreakpoint);
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
        ratingPicked: ratingPicked,
        flashedQuestionId: flashedQuestionId,
        // 题目弹窗
        questionEditor: questionEditor,
        questionForm: questionForm,
        questionSaving: questionSaving,
        questionDraftStatus: questionDraftStatus,
        categoryOptions: categoryOptions,
        tagDraft: tagDraft,
        tagSuggestions: tagSuggestions,
        tagSuggestOpen: tagSuggestOpen,
        tagHighlight: tagHighlight,
        // 记事本
        notesView: notesView,
        noteForm: noteForm,
        noteSearch: noteSearch,
        noteSaving: noteSaving,
        draftStatusText: draftStatusText,
        draftSaving: draftSaving,
        draftPrompt: draftPrompt,
        draftKeysInStorage: draftKeysInStorage,
        // 设置
        settingsForm: settingsForm,
        settingsSaving: settingsSaving,
        // 导出
        exportDialog: exportDialog,
        exportError: exportError,
        selectedQuestionIds: selectedQuestionIds,
        allFilteredSelected: allFilteredSelected,
        toggleSelectQuestion: toggleSelectQuestion,
        toggleSelectAll: toggleSelectAll,
        exportForm: exportForm,
        // 方法
        go: go,
        refreshCurrent: refreshCurrent,
        toggleSidebar: toggleSidebar,
        isMobile: isMobile,
        liveMessage: liveMessage,
        onMenuKeydown: onMenuKeydown,
        toggleUserMenu: toggleUserMenu,
        closeNotif: closeNotif,
        closeUserMenu: closeUserMenu,
        toggleFolder: toggleFolder,
        selectFolder: selectFolder,
        promptNewSubject: promptNewSubject,
        onSearchInput: onSearchInput,
        starQuestion: starQuestion,
        removeQuestion: removeQuestion,
        checkReview: checkReview,
        rateReview: rateReview,
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
        onTagKeydown: onTagKeydown,
        hoverTagSuggest: hoverTagSuggest,
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
