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

  /**
   * 是否禁用周期任务（通知轮询、草稿时间戳刷新）。
   *
   * 仅用于自动化测试：无头浏览器用 `--virtual-time-budget` 快进虚拟时间，
   * 60s 轮询 / 5s 心跳会在预算内被触发很多次，把一次验证拖到几百秒（实测）。
   * 关掉它们对被测的键盘/焦点行为没有任何影响；正常运行不受影响。
   * 由测试页通过 ?nopoll=1 打开。
   */
  var DISABLE_POLLING = typeof location !== 'undefined'
    && /[?&]nopoll=1/.test(location.search);

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
      /** 数据说明页：服务端解析的路径与存在性（不是前端拼的）。 */
      var dataPaths = ref({});
      /**
       * 是否有数据操作在进行中（**必须是布尔**）。
       *
       * 不能传字符串给 :disabled：Vue 3 的运行时把 disabled='' 视为**真**
       * （源码：e => e && (e.disabled || '' === e.disabled)），于是
       * :disabled="'"`（空串）会渲染出 disabled='' 并把按钮**永久禁用**。
       * 实测症状：两个按钮都点不动、没有任何反应。
       */
      var dataBusy = ref(false);
      /** 进行中的是哪个操作：'' | 'backup' | 'export'（只用于按钮文案）。 */
      var dataBusyKind = ref('');
      var dataMessage = ref('');
      var dataError = ref('');
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

      /* ---------------- 题目详情（只读浏览） ---------------- */

      /**
       * 题目详情浮层。
       *
       * 为什么要单独有一个：题目管理页的表格只有"题干预览"纯文本，答案
       * 完全不显示；唯一能看到完整信息的入口是**编辑弹窗** —— 那是改数据
       * 的地方，只是"想看看"不该进那里。复习页虽然能遮答案再揭开，
       * 但它只列今日该复习的题，并不是"我的全部错题"。
       *
       * 这个浮层是**纯只读**的，刻意不放「打勾」：打勾会改复习计划
       * （重置 interval_index、推进 next_review_at），浏览错题不该有副作用。
       * 要打勾请去复习页。
       */
      var questionDetail = reactive({
        open: false,
        question: null,        // 列表项（已含 stem/answer/images/tags）
        review: null,          // GET /questions/{id} 的复习进度，尽力而为
        answerOpen: false,     // 答案默认**遮住**
        copied: false,
      });

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
        /**
         * 题目图片：[{ id, url, kind, isNew }]。
         *
         * `id` 是 question_images 主键 —— `DELETE /upload/image/{id}` 需要它，
         * 少了它就无法真正删除物理文件（只会解关联，照片永久留在 uploads/）。
         * `isNew` = 本次会话刚上传、还没保存过的孤儿图。
         */
        images: [],
      });
      /**
       * 待删除的图片（点「×」时移到这里）。
       *
       * 为什么**不立刻**删：表单是"暂存状态"（本项目有草稿保护，AGENTS 4.5），
       * 点×后又取消保存的话，文件已经从磁盘删掉、表单却还引用它，
       * 保存后就是一个 404 的图。所以统一等**保存成功**后再删。
       *
       * 只收 `isNew` 的图：那种是本次刚上传、且尚未挂到任何已保存题目上的
       * 孤儿，删掉物理文件是安全的。已经保存过的题目图片只做解关联
       * （同一张图可能被别的题引用，后端 delete_image 会连带解绑所有引用 ——
       * 那是破坏性的，不能由"点了个×"触发）。
       */
      var pendingImageDeletes = ref([]);
      /** 正在上传的图片数量（>0 时显示"上传中"并禁用保存，避免半截状态入库）。 */
      var imageUploading = ref(0);
      /** 上传失败的提示（逐条列出，不要只弹 toast 就丢）。 */
      var imageErrors = ref([]);
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

      /**
       * 弹一条提示。
       *
       * `action` 形如 `{ label, handler }` —— 会渲染成一个按钮，
       * 用于「打勾成功 -> 撤销」这类"动作刚发生、要立刻给后悔机会"的场景。
       * 带 action 时默认停留更久（见下），否则用户还没看清就消失了。
       */
      function toast(message, type, sticky, action) {
        toastSeq += 1;
        var id = toastSeq;
        toasts.value.push({
          id: id, message: message, type: type || 'success',
          action: action || null,
        });
        if (!sticky) {
          var ms = action ? 6000 : (type === 'warning' ? 5000 : 3000);
          window.setTimeout(function () { dismissToast(id); }, ms);
        }
        return id;
      }

      /** 点击 Toast 上的动作按钮：先收起提示，再执行回调。 */
      function runToastAction(t) {
        var action = t && t.action;
        dismissToast(t.id);
        if (action && typeof action.handler === 'function') { action.handler(); }
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

      /* ---------------- 通用确认弹窗 ---------------- */

      /**
       * 通用确认框。
       *
       * 为什么需要它：改名/删除文件夹、以及"文件夹下还有题目"这类操作
       * 必须让用户看到**后果**再决定，不能只靠 window.confirm ——
       * 它的文案是纯文本，放不下"仅删除文件夹/取消"这种带后果说明的选择，
       * 而且原生弹窗会阻塞、无法用自检驱动。
       *
       * 用法：`confirmDialog.ask({...})` 返回 Promise<boolean>。
       * 监听 Esc / 点遮罩关闭都会 resolve(false)，不会悬挂。
       */
      var confirmDialog = reactive({
        open: false, title: '', message: '', detail: '',
        confirmLabel: '确定', cancelLabel: '取消',
        danger: false, busy: false, _resolve: null,
      });

      function askConfirm(opts) {
        var o = opts || {};
        // 如果上一个还没结束就先当作取消，避免 Promise 永远悬挂
        if (confirmDialog._resolve) {
          var prev = confirmDialog._resolve;
          confirmDialog._resolve = null;
          prev(false);
        }
        confirmDialog.title = o.title || '确认操作';
        confirmDialog.message = o.message || '';
        confirmDialog.detail = o.detail || '';
        confirmDialog.confirmLabel = o.confirmLabel || '确定';
        confirmDialog.cancelLabel = o.cancelLabel || '取消';
        confirmDialog.danger = !!o.danger;
        confirmDialog.busy = false;
        confirmDialog.open = true;
        rememberFocus();
        return new Promise(function (resolve) {
          confirmDialog._resolve = resolve;
          nextTick(focusIntoModal);
        });
      }

      function settleConfirm(value) {
        var resolve = confirmDialog._resolve;
        confirmDialog._resolve = null;
        confirmDialog.open = false;
        confirmDialog.busy = false;
        restoreFocus();
        if (resolve) { resolve(value); }
      }

      function confirmDialogOk() { settleConfirm(true); }
      function confirmDialogCancel() { settleConfirm(false); }

      /* ---------------- 文件夹改名 / 删除 ---------------- */

      /** 重命名文件夹（学科或大类）。name 必填，后端传 null 会 422。 */
      function renameFolder(folder) {
        if (!folder) { return; }
        var name = window.prompt('重命名「' + folder.name + '」', folder.name);
        if (name === null) { return; }            // 用户取消
        var trimmed = name.trim();
        if (!trimmed || trimmed === folder.name) { return; }
        API.updateFolder(folder.id, { name: trimmed })
          .then(function () {
            toast('已重命名为「' + trimmed + '」');
            return loadFolderTree();
          })
          .catch(function (err) {
            // 同一父下同名会 409/422，转成人话
            if (err && (err.status === 409 || err.status === 422)) {
              toast('同一层里已经有叫「' + trimmed + '」的了', 'warning', true);
              return;
            }
            toastError(err);
          });
      }

      /**
       * 删除文件夹（软删除，requirements 2.2）。
       *
       * **两步走，不直接带 force**：后端在"文件夹（或其子文件夹）下还有
       * 未删除题目"时会返回 409 拒绝删除，这是刻意的安全设计 ——
       * 因为 force 会**连同这些题目一起软删除**（它们在界面上会消失），
       * 必须由用户显式确认。前端要**配合**它：先把后果讲清楚，
       * 确认后才带 force。前端写死 force=true 等于把这个安全阀绕过去。
       */
      function removeFolder(folder) {
        if (!folder) { return; }
        var isSubject = folder.level === 1;
        var kids = folder.children || [];
        var scope = isSubject
          ? '学科「' + folder.name + '」及其下 ' + kids.length + ' 个大类'
          : '大类「' + folder.name + '」';
        var count = folder.question_count || 0;

        var message = '要删除' + scope + '吗？';
        // 文案是纯文本渲染（.confirm__detail 有 pre-line），不要用 markdown 记号，
        // 星号会原样显示出来。
        var detail = count > 0
          ? '它下面还有 ' + count + ' 道题，这些题目会一起被删除。\n'
            + '题目会进入「已删除」状态：数据库里仍保留，但界面上不再显示，'
            + '题目列表与复习队列里都找不到。\n'
            + '它们的图片会变成「无人引用」，可在'
            + '「数据说明页 → 清理孤儿图片」回收。\n'
            + '注意：删除后界面上无法恢复。'
          : '删除后界面上无法恢复。';

        askConfirm({
          title: '删除文件夹',
          message: message,
          detail: detail,
          confirmLabel: count > 0 ? '连同题目一起删除' : '删除',
          danger: true,
        }).then(function (ok) {
          if (!ok) { return; }
          doDeleteFolder(folder, count > 0);
        });
      }

      function doDeleteFolder(folder, force) {
        // deleteFolder(folderId, force:boolean, opts) —— 第二参是布尔
        API.deleteFolder(folder.id, force)
          .then(function (res) {
            // 后端会报出"删了几个文件夹/几道题/几张图变成孤儿"，
            // 直接转述，不要只说"已删除"。带图片时用 sticky 提示
            // 多留一会儿，让用户看清"去清理图片"这句话。
            var text = res && res.message
              ? res.message
              : ('已删除「' + folder.name + '」');
            toast(text, 'success', !!(res && res.images));
            // 删掉的正是当前筛选的文件夹时，清掉筛选条件，
            // 否则题目列表会一直按一个已不存在的 id 过滤（看起来像"题全没了"）
            if (currentFolderId.value === folder.id) {
              currentFolderId.value = null;
            }
            // 连带删了题目，所以复习队列与统计也要一起刷新 ——
            // 否则"今日待复习"角标还挂着已经不存在的题。
            return Promise.all([
              loadFolderTree(), loadQuestions(), loadReview(), loadTags(),
            ]).then(recomputeStats);
          })
          .catch(function (err) {
            if (err && err.status === 409) {
              // 兜底：本地 question_count 可能过期，服务端才是准的。
              // 再问一次，确认后带 force 重发。
              askConfirm({
                title: '还有题目挂在这个文件夹下',
                message: '服务端报告它下面仍有题目。',
                detail: '继续删除会连同这些题目一起软删除：'
                  + '它们会进入「已删除」状态，界面上不再显示。\n'
                  + '它们的图片可在「数据说明页 → 清理孤儿图片」回收。\n'
                  + '注意：删除后界面上无法恢复。',
                confirmLabel: '连同题目一起删除',
                danger: true,
              }).then(function (again) {
                if (again) { doDeleteFolder(folder, true); }
              });
              return;
            }
            toastError(err);
          });
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

      /**
       * 从 hash 解析出页面名。
       *
       * 支持两类：
       *   #/questions              普通页面
       *   #/questions/q/12         题目详情深链接 —— 落到 questions 页，
       *                            再由 applyHashDetail() 打开对应详情
       *
       * 深链接是有意加的：题目详情浮层若不能书签/刷新回到同一题，
       * "进去看一道题"这件事就没法被引用、也没法恢复现场。
       */
      function pageFromHash() {
        var raw = (window.location.hash || '').replace(/^#\/?/, '');
        var base = raw.split('/')[0];
        return VALID_PAGES.indexOf(base) !== -1 ? base : 'home';
      }

      /** hash 里的题目 id（`#/questions/q/12` -> 12），没有则 null。 */
      function detailIdFromHash() {
        var raw = (window.location.hash || '').replace(/^#\/?/, '');
        var m = /^questions\/q\/(\d+)$/.exec(raw);
        return m ? Number(m[1]) : null;
      }

      /**
       * **唯一**改 hash 的地方。
       *
       * 为什么必须收敛到一个函数：之前有 5 处各自 `location.hash = ...`
       * （go / openDetail / closeDetail / applyHashDetail 兜底），
       * 于是"详情已关闭、hash 却还留着 `#/questions/q/{id}`"这种脏状态
       * 某一处漏清就会留下，之后任何一次 hash 同步都会把旧详情**复活** ——
       * 用户看到的是"切到另一个大类，却弹出了刚才那道题"。
       *
       * 同值不赋值：给 location.hash 赋同一个值也会触发 hashchange，
       * 会让 applyHashDetail 白跑一轮。
       */
      function setHash(hash) {
        if (window.location.hash !== hash) {
          window.location.hash = hash;
        }
      }

      /** 普通页面地址（同时清掉可能残留的详情深链接）。 */
      function pageHash(page) {
        return '#/' + (VALID_PAGES.indexOf(page) !== -1 ? page : 'home');
      }

      /** 详情深链接地址。 */
      function detailHash(id) {
        return '#/questions/q/' + id;
      }

      /**
       * 让浮层状态与 hash 中的题目 id 对齐 —— **浮层可见性的唯一权威**。
       * 直接把题目列表里的那一项填进去，不发请求。
       *
       * 三道短路，缺一不可（都是踩过的坑）：
       *
       * 1. `suppressDetailSync`：`closeDetail()` 会改 hash，这个改动的
       *    hashchange（以及紧随的 loadPage 回调）可能在本函数**之后**才跑。
       *    关闭时挂标志、下一个 tick 摘掉，挡住这一小段窗口。
       * 2. `closedDetailId`：用户刚关掉的那一题不许复活。曾经 `closeDetail()`
       *    因为 `if (!open) return` 早退而漏清 hash，之后切大类触发
       *    loadPage -> 本函数 -> 把旧详情又打开了。
       * 3. **不属于当前筛选就不开**：即使 hash 合法，也不该弹出列表里
       *    看不到的题 —— 那会表现为"切到立体几何却弹出线性代数的题"。
       */
      var suppressDetailSync = false;
      /** 用户刚主动关闭的题目 id；再次打开（或换题）时清空。 */
      var closedDetailId = null;

      function applyHashDetail() {
        if (suppressDetailSync) { return; }
        var id = detailIdFromHash();
        if (id === null) {
          // hash 没有题目 id：详情不该开着（只收浮层，hash 已经是对的）
          if (questionDetail.open) { closeDetailSilent(); }
          return;
        }
        // 已经开着同一题：什么都不做
        if (questionDetail.open && questionDetail.question
            && questionDetail.question.id === id) {
          return;
        }
        // 用户刚关掉它：不复活
        if (closedDetailId === id) { return; }
        var q = questions.value.find(function (x) { return x.id === id; });
        if (!q) {
          // 列表里没有（已删除 / 还没加载完）：把 hash 收回普通页面，
          // 否则会出现"地址栏指着某题、界面什么都没打开"的悬空状态
          toast('找不到这道题（可能已删除）', 'warning');
          setHash(pageHash('questions'));
          return;
        }
        // 不属于当前筛选：不在列表里的题不该弹详情
        if (currentFolderId.value && q.folder_id !== currentFolderId.value) {
          setHash(pageHash('questions'));
          return;
        }
        openDetail(q);
      }

      /**
       * 切换页面。
       *
       * `fromNav` 只在**点击侧边栏导航项**时为 true。它决定要不要收起
       * 已打开的题目详情：
       *
       * `#/questions/q/12` 也属于 questions 页，所以"再点一次题目管理"
       * 需要能被理解为"退出详情"，否则用户没有鼠标以外的方式关掉浮层
       * （Esc 之外）。但**程序化跳转不能带上这个副作用** ——
       * `selectFolder()`（切大类）和 `onSearchInput()` 都会调
       * `go('questions')`，曾经因此一点大类就把详情弹窗关掉。
       */
      function go(page, fromNav) {
        if (VALID_PAGES.indexOf(page) === -1) { page = 'home'; }
        var hashIsDetail = detailIdFromHash() !== null;
        currentPage.value = page;
        drawerOpen.value = false;
        userMenuOpen.value = false;

        if (hashIsDetail && fromNav) {
          // 点导航 = 主动退出详情：**先关浮层再改 hash**。
          // 不能只改 hash 指望 hashchange 来关 —— closeDetail 会把 hash
          // 一并清干净，浮层状态才是唯一真相。
          closeDetail();
        } else if (!hashIsDetail) {
          setHash(pageHash(page));
        }
        loadPage(page);
      }

      function onHashChange() {
        var page = pageFromHash();
        if (page !== currentPage.value) {
          currentPage.value = page;
          loadPage(page);
        }
        // 详情浮层跟着 hash 走：刷新、后退、书签都能对齐
        applyHashDetail();
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

      function loadDataPaths() {
        return API.getDataPaths().then(function (p) {
          dataPaths.value = p || {};
        }).catch(function () {
          // 取不到就退回 /health 里的 db_path（页面用 || 兜底），
          // 不要因为这一个只读接口失败就让整页报错。
          dataPaths.value = {};
        });
      }

      /** 手动备份：数据库 + uploads/ 复制到 backups/<时间戳>/。 */
      function doBackup() {
        dataBusy.value = true;
        dataBusyKind.value = 'backup';
        dataMessage.value = '';
        dataError.value = '';
        API.createBackup()
          .then(function (res) {
            dataMessage.value = '备份完成：' + res.backup_dir
              + '（数据库 ' + Math.max(1, Math.round(res.db_bytes / 1024)) + ' KB，'
              + '图片 ' + res.image_count + ' 个）';
            announce('备份完成');
            toast('备份完成');
            return loadDataPaths();
          })
          .catch(function (err) {
            dataError.value = err && err.message ? err.message : '备份失败';
            toastError(err);
          })
          .then(function () { dataBusy.value = false; dataBusyKind.value = ''; });
      }

      /** 导出全部业务数据为 JSON（前端触发下载）。 */
      function doExportData() {
        dataBusy.value = true;
        dataBusyKind.value = 'export';
        dataMessage.value = '';
        dataError.value = '';
        API.exportDataJson()
          .then(function (res) {
            var url = window.URL.createObjectURL(res.blob);
            var a = document.createElement('a');
            a.href = url;
            a.download = res.filename || 'cuotiben-data.json';
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);
            window.URL.revokeObjectURL(url);
            dataMessage.value = '已开始下载 JSON。图片以路径引用，'
              + '完整备份请用「手动备份」。';
            announce('已开始下载 JSON');
            toast('已开始下载 JSON');
          })
          .catch(function (err) {
            dataError.value = err && err.message ? err.message : '导出失败';
            toastError(err);
          })
          .then(function () { dataBusy.value = false; dataBusyKind.value = ''; });
      }

      /* ---------------- 孤儿图片 ---------------- */

      /** 孤儿图片列表与统计（数据说明页显示）。 */
      var orphans = reactive({ count: 0, total_bytes: 0, files: [], note: '' });
      var orphanBusy = ref(false);

      function fmtBytes(n) {
        var v = Number(n) || 0;
        if (v < 1024) { return v + ' B'; }
        if (v < 1024 * 1024) { return (v / 1024).toFixed(1) + ' KB'; }
        return (v / 1024 / 1024).toFixed(1) + ' MB';
      }

      /**
       * 刷新孤儿图片列表。
       *
       * 孤儿 = uploads/ 下存在、但没有任何未删除题目在引用的文件。
       * 典型来源：上传后没保存就关掉弹窗 —— 那种情况下 "保存成功后删除"
       * 的逻辑根本没机会跑，文件就留在磁盘上了。必须有个地方能收掉它们。
       */
      function loadOrphans() {
        orphanBusy.value = true;
        return API.getOrphanImages()
          .then(function (res) {
            orphans.count = res.count || 0;
            orphans.total_bytes = res.total_bytes || 0;
            orphans.files = res.files || [];
            orphans.note = res.note || '';
          })
          .catch(function (err) {
            dataError.value = err && err.message ? err.message : '读取孤儿图片失败';
          })
          .then(function () { orphanBusy.value = false; });
      }

      /** 清理孤儿图片。**二次确认**后才真的删 —— 删除不可撤销。 */
      function doCleanupOrphans() {
        if (!orphans.count) {
          toast('没有需要清理的孤儿图片', 'warning');
          return;
        }
        askConfirm({
          title: '清理孤儿图片',
          message: '将删除 ' + orphans.count + ' 个没有被任何题目引用的图片文件，'
            + '共 ' + fmtBytes(orphans.total_bytes) + '。',
          detail: '只删除这些未被引用的文件，不会动任何题目、也不会动被题目引用的图片。\n'
            + '注意：删除后无法恢复。',
          confirmLabel: '清理',
          danger: true,
        }).then(function (ok) {
          if (!ok) { return; }
          orphanBusy.value = true;
          dataMessage.value = '';
          dataError.value = '';
          API.cleanupOrphanImages()
            .then(function (res) {
              dataMessage.value = '已清理 ' + res.deleted + ' 个文件，'
                + '释放 ' + fmtBytes(res.freed_bytes)
                + (res.failed ? ('；' + res.failed + ' 个删除失败') : '');
              if (res.failed) {
                dataError.value = '有 ' + res.failed + ' 个文件删除失败，'
                  + '可能被其它程序占用：' + (res.failed_files || []).join('、');
              }
              announce('已清理 ' + res.deleted + ' 个孤儿图片');
              toast('已清理 ' + res.deleted + ' 个文件');
              return Promise.all([loadOrphans(), loadDataPaths()]);
            })
            .catch(function (err) {
              dataError.value = err && err.message ? err.message : '清理失败';
              toastError(err);
            })
            .then(function () { orphanBusy.value = false; });
        });
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
          // 复位上一次的备份/导出提示，避免残留上一条结果看起来像本次的
          dataMessage.value = '';
          dataError.value = '';
          jobs.push(loadHealth(), loadDataPaths(), loadOrphans());
          loadDraftKeyList();
        }
        return Promise.all(jobs)
          .then(function () {
            recomputeStats();
            if (page === 'notes') { checkNoteDraft(); }
            // 刷新/书签直接落在 `#/questions/q/12` 时，题目列表这一刻才有数据，
            // 必须在这里再对一次 hash，否则深链接打不开详情
            if (page === 'questions' || page === 'home') {
              if (detailIdFromHash() !== null) { applyHashDetail(); }
            }
          })
          .catch(function (err) { loadError.value = err.message || '加载失败'; })
          .then(function () { loading.value = false; });
      }

      function refreshCurrent() { return loadPage(currentPage.value); }

      /* ================= 文件夹树 ================= */

      function toggleFolder(id) { expandedFolders[id] = !expandedFolders[id]; }

      /**
       * 点击二级大类：切换 currentFolderId 并跳到题目页。
       *
       * 模板上用 `@click.stop` —— 现在侧边栏浮在详情遮罩之上
       * （`.app.has-modal`），点大类的 click 会冒到遮罩上被 `@click.self`
       * 当成"点遮罩"而关掉详情，所以必须在行这一层拦住。
       */
      function selectFolder(id) {
        currentFolderId.value = id;
        // 换到别的大类后，原来那道题若不在新筛选范围内，详情就该收起来 ——
        // 留着它会显示一道"不在当前列表里"的题，容易让人以为列表坏了。
        if (questionDetail.open && questionDetail.question
            && questionDetail.question.folder_id !== id) {
          closeDetail();
        }
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

      /**
       * 在指定学科下新建大类（二级文件夹）。
       *
       * 这个入口以前**根本不存在**：全前端只有 promptNewSubject()，
       * 它不带 parent_id，只能建出 level 1 学科。结果是——用户能建学科，
       * 却建不出大类；而题目只能挂在二级大类下（requirements 2.2/3.10），
       * 于是"新增题目"永远选不到所属大类，被 saveQuestion 的
       * 「请先选择所属大类」挡住，整个录题流程走不通。
       * 后端 POST /folders 一直支持 parent_id，缺的只是这个前端入口。
       */
      function promptNewCategory(subjectId) {
        var subject = folderTree.value.filter(function (s) {
          return s.id === subjectId;
        })[0];
        if (!subject) { return; }

        var name = window.prompt('在「' + subject.name + '」下新建大类名称');
        if (!name || !name.trim()) { return; }
        var trimmed = name.trim();

        API.createFolder({ name: trimmed, parent_id: subjectId })
          .then(function (created) {
            // 展开该学科并选中新大类，用户接着就能直接录题
            expandedFolders[subjectId] = true;
            toast('大类「' + trimmed + '」已创建');
            return loadFolderTree().then(function () {
              if (created && created.id) { currentFolderId.value = created.id; }
            });
          })
          .catch(toastError);
      }

      /** 还没有大类时，让用户能从弹窗里直接去建一个（而不是干瞪眼）。 */
      function newCategoryFromEditor() {
        var subject = folderTree.value[0];
        if (!subject) {
          toast('请先在左侧新建一个学科', 'warning', true);
          return;
        }
        closeQuestionEditor();
        promptNewCategory(subject.id);
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
        // 先把这条队列项留一份：撤销时要用它把题放回原处。
        // 不能撤销时再去问后端要 —— 后端只返回 MessageOut，没有队列项结构。
        var snapshot = item;
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
            toast('已打勾，下次复习：' + next, 'success', false, {
              label: '撤销',
              handler: function () { undoCheck(id, snapshot); },
            });
            announce('已打勾，下次复习 ' + next);
            recomputeStats();
            if (options.flashId) { flashRow(options.flashId); }
            return loadFolderTree();
          })
          .catch(toastError);
      }

      /**
       * 撤销最近一次打勾（requirements 5.3：撤销 = 软删除最近一条 review_record）。
       *
       * 为什么入口挂在 Toast 上而不是做个固定按钮：打勾是"一按即生效、并且
       * 立刻把题从队列移走"的操作，等用户意识到点错时那题已经不在眼前了 ——
       * 撤销必须紧跟着这个动作出现，而不是让用户去找。
       *
       * `snapshot` 是打勾前那条队列项：撤销成功后用它把题**放回队列**。
       * 少了这一步会出现"提示说撤销成功、但队列里还是没有它"。
       */
      function undoCheck(questionId, snapshot) {
        return API.uncheckReview(questionId)
          .then(function () {
            reviewDoneToday.value = Math.max(0, reviewDoneToday.value - 1);
            // 先把本地队列补回去（保留原来的排位），再用服务端结果校正。
            var restored = false;
            if (snapshot) {
              var exists = reviewQueue.value.some(function (i) {
                return i.question_id === questionId;
              });
              if (!exists) {
                reviewQueue.value = reviewQueue.value.concat([snapshot]);
                restored = true;
              }
            }
            toast('已撤销打勾');
            announce('已撤销打勾');
            recomputeStats();
            // 与服务端对齐：撤销后该题应当重新出现在今日队列里
            return Promise.all([loadReview(), loadFolderTree()])
              .then(function () {
                var back = reviewQueue.value.some(function (i) {
                  return i.question_id === questionId;
                });
                if (!back && restored) {
                  // 服务端说不在队列里（比如还没到期），以服务端为准并说明
                  toast('已撤销，但这道题还不到复习时间', 'warning');
                }
              });
          })
          .catch(function (err) {
            // 409 = 已经没有可撤销的记录（比如连点了两次撤销）
            if (err && err.status === 409) {
              toast('这道题没有可撤销的打勾了', 'warning');
              return loadReview();
            }
            toastError(err);
          });
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
        return !!(questionEditor.open || exportDialog.open || previewImage.value
          || confirmDialog.open || questionDetail.open);
      }

      function anyOverlayOpen() {
        return modalIsOpen() || notifOpen.value || userMenuOpen.value;
      }

      /** 关闭所有浮层。Esc 与点击外部都走这里，保证行为一致。 */
      function closeOverlays() {
        // 顺序 = 从最"上"的浮层往下关（后弹的先关）。
        // 确认框与图片预览排前面：它们通常压在别的浮层之上。
        if (confirmDialog.open) { confirmDialogCancel(); return; }
        if (previewImage.value) { previewImage.value = null; restoreFocus(); return; }
        if (questionDetail.open) { closeDetail(); return; }
        if (questionEditor.open) { closeQuestionEditor(); return; }
        if (exportDialog.open) { exportDialog.open = false; restoreFocus(); return; }
        if (notifOpen.value) { closeNotif(); return; }
        if (userMenuOpen.value) { closeUserMenu(); }
      }

      /**
       * 弹窗打开时把焦点移入，并在弹窗内循环（focus trap）。
       * 不这么做的话，Tab 会跑到弹窗背后的侧边栏与顶部栏上 ——
       * 视觉上焦点"消失"在遮罩后面，键盘用户无法操作弹窗。
       */
      /**
 * 遮罩上的 @mousedown.self.prevent 为什么必须带 .self：
 *
 * 不带的写法（绑在 .modal-mask 上、不限定目标）会让**弹窗内部**的
 * mousedown 也冒泡上来被 preventDefault。浏览器"点击聚焦"属于默认行为，
 * 被阻止后输入框拿不到焦点 —— 表现就是"点输入框点不进去、打字落到了
 * 上次聚焦的按钮上、下拉也点不开"，而按钮点击仍然正常（按钮靠 click 触发，
 * 不依赖聚焦），所以现象看起来很像"只有文字输入坏了"。
 *
 * 加 .self 后：只有直接点在遮罩空白处才阻止默认行为（避免焦点跑到 body、
 * 关闭时无法归还），点在弹窗内部完全不影响。
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

      // 五类弹窗任一打开即锁滚动（题目编辑 / 导出 / 图片预览 / 确认框 / 题目详情）。
      // 用 watch 而不是散落在各 open/close 里，避免将来新增弹窗时漏掉
      // 其中一条路径（Esc / 遮罩 / 按钮都要还原）。
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

      /**
       * 列表里的题干缩略图（题干图的第一张），没有则返回空串。
       *
       * 为什么要它：纯图片题目在列表里只显示"（无题干，仅图片）"，
       * 光看文字根本认不出是哪道题 —— 扫一眼缩略图才知道。
       */
      function stemThumb(q) {
        if (!q) { return ''; }
        var imgs = (q.images || []).filter(function (i) {
          return (i.kind || 'stem') === 'stem';
        });
        return imgs.length ? imageUrl(imgs[0].file_path) : '';
      }

      /* ---------------- 题目详情浮层 ---------------- */

      /** 题干图（kind='stem'）。 */
      function detailStemImages() {
        var q = questionDetail.question;
        if (!q) { return []; }
        return (q.images || []).filter(function (i) {
          return (i.kind || 'stem') === 'stem';
        });
      }

      /** 答案图（kind='answer'）。 */
      function detailAnswerImages() {
        var q = questionDetail.question;
        if (!q) { return []; }
        return (q.images || []).filter(function (i) {
          return i.kind === 'answer';
        });
      }

      /** 这道题是否只有图、没有文字（用于给出更有用的提示）。 */
      function detailIsImageOnly() {
        var q = questionDetail.question;
        if (!q) { return false; }
        var noText = !(q.stem || '').trim() && !(q.answer || '').trim();
        return noText && (q.images || []).length > 0;
      }

      /**
       * 打开题目详情。
       *
       * 先把列表项直接填进去 —— `GET /questions` 返回的每项**已经含**
       * stem/answer/images/tags，所以打开是**瞬时的、不发请求**；
       * 复习进度（interval_index / review_count）列表里没有，再补一次
       * 单题请求，且失败也不影响浏览（`review` 保持 null，界面就不显示那一段）。
       */
      function openDetail(q) {
        if (!q || !q.id) { return; }
        questionDetail.question = q;
        questionDetail.review = null;
        questionDetail.answerOpen = false;   // 每次打开都重新遮住
        questionDetail.copied = false;
        questionDetail.open = true;
        closedDetailId = null;               // 明确打开了，解除"刚关掉"的封印
        rememberFocus();
        // 弹窗打开必须把焦点移入，否则键盘用户按 Tab 会从一个"在背后的"
        // 元素开始走，视觉上焦点像是凭空出现。见 AGENTS.md 第十节 10.1。
        nextTick(focusIntoModal);
        // 深链接：刷新/书签能直接回到这道题
        setHash(detailHash(q.id));
        // 复习进度：纯附加信息，失败静默
        API.getQuestion(q.id)
          .then(function (full) {
            if (questionDetail.open
                && questionDetail.question
                && questionDetail.question.id === q.id) {
              questionDetail.review = full;
            }
          })
          .catch(function () { /* 进度取不到不影响看题 */ });
      }

      /**
       * 关闭详情（**用户入口**：Esc / 点遮罩 / 按钮 / 点导航）。
       *
       * 永远把 hash 清回普通页面 —— `#/questions/q/{id}` 是"我正在看第 id 题"
       * 的承诺，浮层关了这个承诺就失效了。曾经因为
       * `if (!questionDetail.open) return;` 早退而漏清 hash，残留的地址
       * 之后会被 applyHashDetail **兑现**，表现为"切到立体几何，却弹出了
       * 线性代数那道题"。
       *
       * **刻意不接受任何参数**：模板上写 `@click.self="closeDetail"` 时，
       * Vue 会把 MouseEvent 当作第一个参数传进来。以前签名是
       * `closeDetail(keepHash)`，于是 `keepHash` 变成那个**真值事件对象**，
       * `if (!keepHash)` 永远不成立 —— hash 就永远清不掉。
       * 这是很容易重犯的坑，所以把"用不用 keepHash"拆成两个函数：
       * 这个只给用户操作调，内部同步用 closeDetailSilent。
       */
      function closeDetail() {
        closeDetailSilent();
        setHash(pageHash('questions'));
      }

      /**
       * 关闭详情但**不动 hash**（内部用）。
       *
       * 唯一调用点是 `applyHashDetail()`：hash 已经指向普通页面、只是浮层
       * 还开着时，收掉浮层即可，不必再写一次 hash（写同值也会触发
       * hashchange，白跑一轮）。
       *
       * **不导出、不绑到模板**，避免又被当成事件监听器直接传。
       */
      function closeDetailSilent() {
        if (!questionDetail.open) { return; }
        suppressDetailSync = true;
        // 记下"刚被用户关掉的是哪一题"：applyHashDetail 不许它复活
        closedDetailId = questionDetail.question
          ? questionDetail.question.id : null;
        questionDetail.open = false;
        questionDetail.question = null;
        questionDetail.review = null;
        questionDetail.answerOpen = false;
        // 先归还焦点再让 DOM 移除，否则 lastFocused 会被 body 覆盖
        restoreFocus();
        // 等这一轮 hashchange / loadPage 回调都跑完再放开
        nextTick(function () {
          window.setTimeout(function () { suppressDetailSync = false; }, 0);
        });
      }

      function toggleDetailAnswer() {
        questionDetail.answerOpen = !questionDetail.answerOpen;
      }

      /** 从详情直接跳到编辑弹窗（省得关掉再去表里找那一行）。 */
      function editFromDetail() {
        var q = questionDetail.question;
        closeDetail();
        if (q) { openQuestionEditor(q); }
      }

      /** 复制题干（纯图片题目的题干可能很长或是代码）。 */
      function copyDetailStem() {
        var q = questionDetail.question;
        var text = q ? (q.stem || '') : '';
        if (!text) { toast('这道题没有文字题干', 'warning'); return; }
        var done = function () {
          questionDetail.copied = true;
          toast('题干已复制');
          window.setTimeout(function () { questionDetail.copied = false; }, 2000);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done).catch(function () {
            toast('复制失败，请手动选择文本', 'warning');
          });
        } else {
          toast('当前环境不支持自动复制', 'warning');
        }
      }

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
        questionForm.images = q
          ? (q.images || []).map(function (img) {
              return {
                id: img.id,
                url: imageUrl(img.file_path),
                kind: img.kind || 'stem',
                isNew: false,
              };
            })
          : [];
        pendingImageDeletes.value = [];
        imageErrors.value = [];
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

      /* ---------------- 题目图片（题干图 / 答案图）---------------- */

      /**
       * 把任意写法的图片路径规范成可直接放进 `src` 的 URL。
       *
       * 后端有**两种**写法，混用会出问题：
       *   - `file_path`：库内相对路径 `uploads/2026/10/01/x.jpg`（不带前导斜杠）
       *   - `url`      ：可访问地址   `/uploads/2026/10/01/x.jpg`
       * 直接把 file_path 塞进 `src`，在首页(/)上凑巧能加载，但换到任何
       * 子路径就会 404 —— 而这种问题在本地手测时极难发现。
       *
       * 所以渲染前一律过一遍这里，缺前导斜杠就补上。
       */
      function imageUrl(path) {
        var text = String(path || '').replace(/\\/g, '/').trim();
        if (!text) { return ''; }
        if (/^(https?:)?\/\//.test(text) || text.indexOf('data:') === 0) {
          return text;                       // 外链或 data URL 原样返回
        }
        if (text.charAt(0) !== '/') { text = '/' + text; }
        return text;
      }

      /** 取某个位置的图片（'stem' / 'answer'）。 */
      function imagesOf(kind) {
        return questionForm.images.filter(function (i) { return i.kind === kind; });
      }

      /**
       * 处理 <input type="file"> 的选择：逐张上传，成功后追加到对应位置。
       *
       * 为什么允许多选、并且**逐张独立**上传：一张失败不该把其余几张一起废掉；
       * 每张上传成功就立刻进 images 数组，用户能马上看到缩略图。
       */
      function onPickImages(event, kind) {
        var input = event.target;
        var files = Array.prototype.slice.call(input.files || []);
        input.value = '';                        // 允许连续选同一个文件
        if (!files.length) { return; }
        imageErrors.value = [];

        files.forEach(function (file) {
          imageUploading.value += 1;
          API.uploadImage(file)
            .then(function (res) {
              if (!res || !res.file_path) {
                throw new Error('上传接口未返回 file_path');
              }
              questionForm.images.push({
                id: res.id, url: imageUrl(res.file_path), kind: kind, isNew: true,
              });
              onQuestionEdit();
            })
            .catch(function (err) {
              var name = file && file.name ? file.name : '（未命名）';
              imageErrors.value.push(name + '：' +
                (err && err.message ? err.message : '上传失败'));
            })
            .then(function () { imageUploading.value -= 1; });
        });
      }

      /** 从表单里移除一张图。真正删文件在保存成功之后（见 pendingImageDeletes）。 */
      function removeQuestionImage(kind, url) {
        var removed = questionForm.images.filter(function (i) {
          return i.kind === kind && i.url === url;
        });
        questionForm.images = questionForm.images.filter(function (i) {
          return !(i.kind === kind && i.url === url);
        });
        // 本次会话刚上传、还没保存过的图：记下来，保存成功后删掉文件
        removed.forEach(function (i) {
          if (i.isNew && i.id) { pendingImageDeletes.value.push(i.id); }
        });
        onQuestionEdit();
      }

      /**
       * 保存成功后清理"被移除的新上传图片"。
       *
       * 失败不打扰用户：物理文件没删掉只是占点磁盘，而弹一个错误提示会
       * 让人以为保存失败了。只记录到控制台，并让 /data/paths 的图片计数
       * 能反映出真实情况。
       */
      function flushPendingImageDeletes() {
        var ids = pendingImageDeletes.value.slice();
        pendingImageDeletes.value = [];
        ids.forEach(function (imageId) {
          API.deleteImage(imageId).catch(function (err) {
            // eslint-disable-next-line no-console
            console.warn('[image] 删除图片 ' + imageId + ' 失败：',
              err && err.message ? err.message : err);
          });
        });
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
        if (imageUploading.value > 0) {
          toast('图片还在上传，请稍候', 'warning', true);
          return;
        }
        questionSaving.value = true;
        var payload = {
          folder_id: questionForm.folder_id,
          stem: questionForm.stem === '' ? null : questionForm.stem,
          answer: questionForm.answer === '' ? null : questionForm.answer,
          tags: questionForm.tags,
          is_starred: questionForm.is_starred,
          // 契约是 [{url, kind}]，不是裸 URL 数组（requirements 3.3）
          images: questionForm.images.map(function (i) {
            return { url: i.url, kind: i.kind };
          }),
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
          // 保存已成功，此时删掉"本次上传又被移除"的图是安全的
          flushPendingImageDeletes();
          return Promise.all([loadQuestions(), loadFolderTree()]);
        }).then(function () {
          questionSaving.value = false;
          if (continueAdding) {
            questionForm.stem = '';
            questionForm.answer = '';
            questionForm.tags = [];
            questionForm.images = [];
            imageErrors.value = [];
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
        if (!DISABLE_POLLING) {
          // 通知每 60s 轮询（requirements 2.9 要求 30~60s）
          pollTimer = window.setInterval(function () {
            loadReview().then(recomputeStats).catch(function () { /* 静默 */ });
          }, 60000);
          savedAtTicker = window.setInterval(tickSavedAt, 5000);
        }
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
        /**
         * 模板要用它给 `.app` 加 `has-modal` 类，从而把侧边栏抬到弹窗遮罩之上
         * （`.app.has-modal .sidebar`，见 style.css）。不导出的话这个类永远
         * 加不上，弹窗一开侧边栏就被遮罩盖住、点不到大类行。
         */
        modalIsOpen: modalIsOpen,
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
        dataPaths: dataPaths,
        dataBusy: dataBusy,
        dataBusyKind: dataBusyKind,
        dataMessage: dataMessage,
        dataError: dataError,
        doBackup: doBackup,
        doExportData: doExportData,
        questionDetail: questionDetail,
        openDetail: openDetail,
        closeDetail: closeDetail,
        toggleDetailAnswer: toggleDetailAnswer,
        editFromDetail: editFromDetail,
        copyDetailStem: copyDetailStem,
        detailStemImages: detailStemImages,
        detailAnswerImages: detailAnswerImages,
        detailIsImageOnly: detailIsImageOnly,
        stemThumb: stemThumb,
        orphans: orphans,
        orphanBusy: orphanBusy,
        fmtBytes: fmtBytes,
        loadOrphans: loadOrphans,
        doCleanupOrphans: doCleanupOrphans,
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
        onPickImages: onPickImages,
        removeQuestionImage: removeQuestionImage,
        imagesOf: imagesOf,
        imageUrl: imageUrl,
        pendingImageDeletes: pendingImageDeletes,
        imageUploading: imageUploading,
        imageErrors: imageErrors,
        promptNewSubject: promptNewSubject,
        promptNewCategory: promptNewCategory,
        newCategoryFromEditor: newCategoryFromEditor,
        renameFolder: renameFolder,
        removeFolder: removeFolder,
        confirmDialog: confirmDialog,
        confirmDialogOk: confirmDialogOk,
        confirmDialogCancel: confirmDialogCancel,
        onSearchInput: onSearchInput,
        starQuestion: starQuestion,
        removeQuestion: removeQuestion,
        checkReview: checkReview,
        undoCheck: undoCheck,
        runToastAction: runToastAction,
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
