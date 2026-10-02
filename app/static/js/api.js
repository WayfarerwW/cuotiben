/**
 * 错题本 · 后端接口封装
 * ---------------------------------------------------------------------------
 * 约定（AGENTS.md 3.2：API 调用统一走本文件）：
 *   - 只暴露一个全局对象 `API`，页面脚本通过 `API.xxx()` 调用，不直接写 fetch
 *   - 统一用 fetch，非 2xx 一律抛出 ApiError（失败必须可被 catch 捕获，
 *     不允许静默返回 undefined 让调用方以为成功）
 *   - 本文件不依赖任何框架与构建工具，可直接作为普通 <script> 引入
 *     （无 import/export，离线本地运行）
 *
 * 字段语义提醒（AGENTS.md 3.4 的 PUT 规则）：
 *   - PUT 的 null 有明确含义：可空字段传 null 表示清空；不可空字段传 null 会 422
 *   - 因此本文件构造 body 时**保留显式的 null**，只丢弃 undefined，
 *     否则"清空"这个意图会被前端自己吃掉
 *
 * 后端接口清单见 docs/requirements.md 第 4 节。
 */

(function (global) {
  'use strict';

  /** 服务基址。同源部署时留空即可（页面由后端直接提供）。 */
  var BASE_URL = '';

  /**
   * 设置服务基址。
   * 浏览器里页面与接口同源时不需要调用；Node 自检或前后端不同源时用它指定，
   * 例如 API.configure({ baseUrl: 'http://127.0.0.1:8000' })。
   */
  function configure(options) {
    var opts = options || {};
    if (typeof opts.baseUrl === 'string') {
      BASE_URL = opts.baseUrl.replace(/\/+$/, '');
    }
    return BASE_URL;
  }

  /**
   * 统一错误类型。
   * 携带 status / detail / url，便于调用方区分"参数错误(422)"、"不存在(404)"
   * 与"网络不通"，而不是只拿到一句字符串。
   */
  function ApiError(message, options) {
    var opts = options || {};
    var err = Error.call(this, message);
    this.name = 'ApiError';
    this.message = message;
    this.status = opts.status === undefined ? 0 : opts.status;
    this.statusText = opts.statusText || '';
    this.detail = opts.detail === undefined ? null : opts.detail;
    this.url = opts.url || '';
    this.cause = opts.cause || null;
    if (Error.captureStackTrace) {
      Error.captureStackTrace(this, ApiError);
    } else {
      this.stack = err.stack;
    }
  }
  ApiError.prototype = Object.create(Error.prototype);
  ApiError.prototype.constructor = ApiError;

  /**
   * 把 FastAPI 的错误体整理成可读消息。
   * 422 的 detail 是数组（每个元素含 loc/msg），404/409 是字符串。
   */
  function describeDetail(detail, status) {
    if (typeof detail === 'string' && detail) {
      return detail;
    }
    if (Array.isArray(detail)) {
      var parts = detail.map(function (item) {
        var loc = Array.isArray(item.loc) ? item.loc.join('.') : '';
        var msg = item.msg || '';
        return loc ? loc + ': ' + msg : msg;
      });
      if (parts.length) {
        return parts.join('; ');
      }
    }
    return 'HTTP ' + status;
  }

  /** 把对象序列化成查询串；数组按重复 key 展开，null/undefined 跳过。 */
  function buildQuery(params) {
    if (!params) {
      return '';
    }
    var pairs = [];
    Object.keys(params).forEach(function (key) {
      var value = params[key];
      if (value === undefined || value === null) {
        return;
      }
      var list = Array.isArray(value) ? value : [value];
      list.forEach(function (item) {
        if (item === undefined || item === null) {
          return;
        }
        pairs.push(encodeURIComponent(key) + '=' + encodeURIComponent(item));
      });
    });
    return pairs.length ? '?' + pairs.join('&') : '';
  }

  /**
   * 构造请求体：保留显式 null（表示清空），丢弃 undefined（表示未传）。
   * 这是 PUT 语义能正确表达的前提。
   */
  function buildBody(data) {
    if (data === undefined || data === null) {
      return undefined;
    }
    var out = {};
    var has = false;
    Object.keys(data).forEach(function (key) {
      if (data[key] !== undefined) {
        out[key] = data[key];
        has = true;
      }
    });
    return has ? out : {};
  }

  /** 从响应头里取文件名（后端用 Content-Disposition 给出）。 */
  function filenameFrom(response, fallback) {
    var disposition = response.headers.get('Content-Disposition') || '';
    var utf8 = /filename\*=UTF-8''([^;]+)/i.exec(disposition);
    if (utf8) {
      try {
        return decodeURIComponent(utf8[1]);
      } catch (e) {
        /* 忽略解码失败，回退下面的普通 filename */
      }
    }
    var plain = /filename="?([^";]+)"?/i.exec(disposition);
    return plain ? plain[1] : fallback;
  }

  /**
   * 统一请求入口。
   *
   * @param {string} path    以 / 开头的接口路径
   * @param {object} [opts]
   * @param {string} [opts.method='GET']
   * @param {object} [opts.query]   查询参数（数组会展开为重复 key）
   * @param {object} [opts.body]    请求体（保留显式 null）
   * @param {string} [opts.responseType='json'] 'json' | 'blob' | 'none'
   * @param {AbortSignal} [opts.signal] 用于取消（如搜索防抖时取消上一次）
   * @returns {Promise<any>}
   */
  function request(path, opts) {
    var options = opts || {};
    var method = options.method || 'GET';
    // 已经是绝对地址时不再拼 BASE_URL（便于指向其它主机做自检）
    var isAbsolute = /^https?:\/\//i.test(path);
    var url = (isAbsolute ? '' : BASE_URL) + path + buildQuery(options.query);
    var init = { method: method, headers: {} };

    var body = buildBody(options.body);
    if (body !== undefined) {
      init.headers['Content-Type'] = 'application/json';
      init.body = JSON.stringify(body);
    }
    if (options.signal) {
      init.signal = options.signal;
    }

    return global.fetch(url, init).then(
      function (response) {
        var type = options.responseType || 'json';

        if (!response.ok) {
          // 先尝试解析错误体，拿不到就退化为状态码说明
          return response
            .json()
            .catch(function () {
              return null;
            })
            .then(function (payload) {
              var detail = payload && payload.detail !== undefined
                ? payload.detail
                : null;
              throw new ApiError(describeDetail(detail, response.status), {
                status: response.status,
                statusText: response.statusText,
                detail: detail,
                url: url,
              });
            });
        }

        if (type === 'none' || response.status === 204) {
          return null;
        }
        if (type === 'blob') {
          return response.blob().then(function (blob) {
            return { blob: blob, filename: filenameFrom(response, 'export.pdf') };
          });
        }
        // 200/201 但无内容时返回 null，避免 json() 抛解析错误
        var length = response.headers.get('Content-Length');
        if (length === '0') {
          return null;
        }
        return response.json().catch(function () {
          return null;
        });
      },
      function (cause) {
        // fetch 本身失败（断网、服务未启动、请求被 abort）
        if (cause && cause.name === 'AbortError') {
          throw cause; // 取消不是错误，原样抛出交给调用方忽略
        }
        throw new ApiError('无法连接后端服务，请确认服务已启动', {
          status: 0,
          url: url,
          cause: cause,
        });
      }
    );
  }

  /* ======================= 文件夹（requirements 4.1）======================= */

  var folders = {
    /** 完整两级树（含 children 与 question_count）。 */
    getFolderTree: function (opts) {
      return request('/folders/tree', opts);
    },
    /** 新建。parent_id 传 null/省略 => 一级学科。 */
    createFolder: function (data, opts) {
      return request('/folders', Object.assign({ method: 'POST', body: data }, opts));
    },
    /** 重命名（name 必填，传 null 会 422）。 */
    updateFolder: function (folderId, data, opts) {
      return request('/folders/' + folderId, Object.assign(
        { method: 'PUT', body: data }, opts));
    },
    /**
     * 软删除文件夹。**会连同其下题目一起软删除**（requirements 2.2）。
     *
     * 文件夹下有未删除题目时默认 409 拒绝；传 `force=true` 表示已确认
     * "这一整块都不要了"，此时连同这些题目一并软删除。
     * 返回 `{ ok, message, folders, questions, images }` ——
     * `images` 是"这些题挂了几张图"，它们此刻变成孤儿，
     * 可用「数据说明页 → 清理孤儿图片」回收。
     */
    deleteFolder: function (folderId, force, opts) {
      return request('/folders/' + folderId, Object.assign(
        { method: 'DELETE', query: { force: force === true } }, opts));
    },
  };

  /* ======================= 题目（requirements 4.2）======================= */

  var questions = {
    /**
     * 列表与筛选。
     * @param {object} [filters]
     * @param {number} [filters.folder_id]
     * @param {string[]} [filters.tag]      可传多个，后端按 tag_mode 组合
     * @param {string} [filters.tag_mode]   'and'（默认）| 'or'
     * @param {string} [filters.keyword]    只匹配题干与答案，**不匹配标签**
     * @param {boolean} [filters.starred]
     * @param {string} [filters.mastery]    'still_wrong' | 'mastered'
     */
    getQuestions: function (filters, opts) {
      return request('/questions', Object.assign({ query: filters }, opts));
    },
    getQuestion: function (questionId, opts) {
      return request('/questions/' + questionId, opts);
    },
    /** 创建。stem/answer 可空（允许纯图片题目）；tags/images 为数组。 */
    createQuestion: function (data, opts) {
      return request('/questions', Object.assign({ method: 'POST', body: data }, opts));
    },
    /**
     * 编辑。只改传入的字段：
     *   - 未传 -> 保持原值
     *   - stem/answer 传 null -> 清空
     *   - folder_id/is_starred/mastery_status/sort_order/tags/images 传 null -> 422
     */
    updateQuestion: function (questionId, data, opts) {
      return request('/questions/' + questionId, Object.assign(
        { method: 'PUT', body: data }, opts));
    },
    deleteQuestion: function (questionId, opts) {
      return request('/questions/' + questionId, Object.assign(
        { method: 'DELETE' }, opts));
    },
    /**
     * 标记/取消重点。
     * 后端是两个接口（/star 与 /unstar），这里按前端的"切换"语义收拢成一个：
     * 省略 starred 时依据当前值取反，需要时先查一次详情。
     */
    toggleStar: function (questionId, starred, opts) {
      var self = this;
      var decide = function (target) {
        var path = '/questions/' + questionId + (target ? '/star' : '/unstar');
        return request(path, Object.assign({ method: 'POST' }, opts));
      };
      if (starred === undefined || starred === null) {
        return self.getQuestion(questionId, opts).then(function (question) {
          return decide(!question.is_starred);
        });
      }
      return decide(starred === true);
    },
    /**
     * 切换/设置正误状态。
     * 省略 masteryStatus 时后端在两个状态之间翻转
     * （AGENTS.md 3.4：状态类接口不传 body 为翻转、传 body 为直接设置）。
     */
    toggleMastery: function (questionId, masteryStatus, opts) {
      var body;
      if (masteryStatus !== undefined && masteryStatus !== null) {
        body = { mastery_status: masteryStatus };
      }
      return request('/questions/' + questionId + '/mastery', Object.assign(
        { method: 'POST', body: body }, opts));
    },
  };

  /* ======================= 复习（requirements 4.5）======================= */

  var review = {
    /** 今日队列（今日到期 + 补卡，含 overdue_days / is_backlog）。 */
    getReviewToday: function (opts) {
      return request('/review/today', opts);
    },
    /** 待复习数量，返回 { count }。 */
    getReviewCount: function (opts) {
      return request('/review/count', opts);
    },
    /**
     * 打勾。任何题、任何时间都能打（不校验待复习状态，不返回 409）。
     * @param {number} [mastery] 0~3，省略按 0 处理
     */
    checkReview: function (questionId, mastery, opts) {
      var body;
      if (mastery !== undefined && mastery !== null) {
        body = { mastery: mastery };
      }
      return request('/review/' + questionId + '/check', Object.assign(
        { method: 'POST', body: body }, opts));
    },
    /** 撤销：软删除最近一条复习记录。没有可撤销记录时 404。 */
    uncheckReview: function (questionId, opts) {
      return request('/review/' + questionId + '/uncheck', Object.assign(
        { method: 'POST' }, opts));
    },
    /**
     * 一键重置积压（逾期 >= backfill_reset_days）。
     * @param {boolean} [spread=false] true 为分散重置到未来 N 天
     * @param {number} [days] 分散窗口，仅在 spread=true 时生效
     */
    resetBackfill: function (spread, days, opts) {
      var body = {};
      if (spread !== undefined) {
        body.spread = spread === true;
      }
      if (days !== undefined && days !== null) {
        body.days = days;
      }
      return request('/review/backfill/reset', Object.assign(
        { method: 'POST', body: body }, opts));
    },
    /** 补卡统计：今日补卡数量 / 连续补卡天数 / 当前积压数。 */
    getBackfillStats: function (opts) {
      return request('/review/backfill/stats', opts);
    },
  };

  /* ======================= 标签（requirements 4.3）======================= */

  var tags = {
    /** 全部标签，按 question_count 降序、同数按 name 升序。 */
    getTags: function (opts) {
      return request('/tags', opts);
    },
    /** 联想：模糊匹配，默认最多 20 条。查询词会做与入库相同的归一化。 */
    searchTags: function (q, limit, opts) {
      return request('/tags/search', Object.assign(
        { query: { q: q, limit: limit } }, opts));
    },
  };

  /* ======================= 记事本（requirements 4.6）======================= */

  var notes = {
    /** 列表，按 updated_at 倒序；content 超过 200 字会截断。 */
    getNotes: function (opts) {
      return request('/notes', opts);
    },
    /** 详情，返回完整 content。 */
    getNote: function (noteId, opts) {
      return request('/notes/' + noteId, opts);
    },
    /** 新建。title/content 可空。 */
    createNote: function (data, opts) {
      return request('/notes', Object.assign({ method: 'POST', body: data }, opts));
    },
    /** 编辑。只改传入字段，传 null 表示清空。 */
    updateNote: function (noteId, data, opts) {
      return request('/notes/' + noteId, Object.assign(
        { method: 'PUT', body: data }, opts));
    },
    deleteNote: function (noteId, opts) {
      return request('/notes/' + noteId, Object.assign({ method: 'DELETE' }, opts));
    },
    /** 搜索：同时匹配标题与内容；q 为空时后端返回空数组。 */
    searchNotes: function (q, limit, opts) {
      return request('/notes/search', Object.assign(
        { query: { q: q, limit: limit } }, opts));
    },
  };

  /* ======================= 设置（requirements 4.8）======================= */

  var settings = {
    /** { intervals, backfill_limit, backfill_reset_days } */
    getSettings: function (opts) {
      return request('/settings', opts);
    },
    /**
     * 批量更新。只改传入的项：
     *   - 未传 -> 保持原值
     *   - 传 null -> 删除该配置项、回退默认值
     */
    updateSettings: function (data, opts) {
      return request('/settings', Object.assign({ method: 'PUT', body: data }, opts));
    },
  };

  /* ======================= 导出（requirements 4.7）======================= */

  var exportApi = {
    /**
     * 导出 PDF，返回 { blob, filename }，由调用方触发下载。
     *
     * `filename` 从响应的 Content-Disposition 里解析（后端按 RFC 5987 给出
     * `filename*=UTF-8''…`，带日期与范围标识）。后端实现见
     * app/routers/export.py，中文渲染用 fonts/NotoSansSC-VF.ttf。
     *
     * @param {object} [data] 导出范围与选项，形如
     *   { scope: 'folder'|'tags'|'starred'|'review_queue'|'manual',
     *     folder_id, tags, question_ids, with_answer, include_tags }
     *   scope=manual 时必须给 question_ids；scope=tags 时必须给 tags，
     *   否则后端返回 422。
     */
    exportPDF: function (data, opts) {
      return request('/export/pdf', Object.assign(
        { method: 'POST', body: data, responseType: 'blob' }, opts));
    },
  };

  /* ======================= 数据管理（requirements 2.17）======================= */

  var dataApi = {
    /**
     * 数据路径与实际存在性：
     * { db_path, db_exists, uploads_path, uploads_exists,
     *   uploads_file_count, backups_path, backups_exists }
     *
     * 路径由**服务端**用当前生效的配置解析（CUOTIBEN_DATABASE_URL 可覆盖），
     * 前端不要自己拼路径 —— 否则页面显示的可能是另一个文件。
     */
    getPaths: function (opts) {
      return request('/data/paths', opts);
    },
    /**
     * 手动备份：把数据库与 uploads/ 复制到 backups/<时间戳>/。
     * 返回 { backup_dir, db_bytes, image_count, created_at }。
     */
    createBackup: function (opts) {
      return request('/data/backup', Object.assign({ method: 'POST' }, opts));
    },
    /** 导出全部业务数据为 JSON，返回 { blob, filename }。 */
    exportDataJson: function (opts) {
      return request('/data/export', Object.assign({ responseType: 'blob' }, opts));
    },
    /**
     * 列出孤儿图片（uploads/ 下没有任何未删除题目引用的文件）。
     * 返回 { count, total_bytes, files: [{file_path, url, size}], note }。**只读**。
     */
    getOrphanImages: function (opts) {
      return request('/upload/orphans', opts);
    },
    /**
     * 清理孤儿图片，返回 { found, deleted, failed, freed_bytes, ... }。
     * 服务端会校验路径必须仍在 uploads/ 之内（防目录穿越）。
     */
    cleanupOrphanImages: function (opts) {
      return request('/upload/cleanup', Object.assign({ method: 'POST' }, opts));
    },
  };

  /* ======================= 其它（未在本步要求内，按需使用）======================= */

  var misc = {
    /** 服务状态与数据库位置：{ status, db_path, db_exists } */
    getHealth: function (opts) {
      return request('/health', opts);
    },
    /**
     * 上传并压缩图片（宽 1080px / JPEG q75 / ≤300KB）。
     * 返回 { id, url, file_path, width, height, size, fell_back_to_original }；
     * id 用于删除图片（DELETE /upload/image/{id}）。
     * 注意：上传用 multipart，不经 request 的 JSON 通道。
     */
    uploadImage: function (file, opts) {
      var options = opts || {};
      var form = new FormData();
      form.append('file', file);
      var init = { method: 'POST', body: form };
      if (options.signal) {
        init.signal = options.signal;
      }
      return global.fetch(BASE_URL + '/upload/image', init).then(function (response) {
        if (!response.ok) {
          return response.json().catch(function () {
            return null;
          }).then(function (payload) {
            var detail = payload && payload.detail !== undefined ? payload.detail : null;
            throw new ApiError(describeDetail(detail, response.status), {
              status: response.status,
              detail: detail,
              url: BASE_URL + '/upload/image',
            });
          });
        }
        return response.json();
      });
    },
    /** 按 question_images 主键删除图片（同时解绑题目关联并删物理文件）。 */
    deleteImage: function (imageId, opts) {
      return request('/upload/image/' + imageId, Object.assign(
        { method: 'DELETE' }, opts));
    },
  };

  var API = {
    configure: configure,
    ApiError: ApiError,
    request: request,
    folders: folders,
    questions: questions,
    review: review,
    tags: tags,
    notes: notes,
    settings: settings,
    export: exportApi,
    misc: misc,

    // --- 扁平别名：便于页面里直接写 API.getQuestions(...) ---
    getFolderTree: folders.getFolderTree,
    createFolder: folders.createFolder,
    updateFolder: folders.updateFolder,
    deleteFolder: folders.deleteFolder,

    getQuestions: questions.getQuestions,
    getQuestion: questions.getQuestion,
    createQuestion: questions.createQuestion,
    updateQuestion: questions.updateQuestion,
    deleteQuestion: questions.deleteQuestion,
    toggleStar: questions.toggleStar,
    toggleMastery: questions.toggleMastery,

    getReviewToday: review.getReviewToday,
    getReviewCount: review.getReviewCount,
    checkReview: review.checkReview,
    uncheckReview: review.uncheckReview,
    resetBackfill: review.resetBackfill,
    getBackfillStats: review.getBackfillStats,

    getTags: tags.getTags,
    searchTags: tags.searchTags,

    getNotes: notes.getNotes,
    getNote: notes.getNote,
    createNote: notes.createNote,
    updateNote: notes.updateNote,
    deleteNote: notes.deleteNote,
    searchNotes: notes.searchNotes,

    getSettings: settings.getSettings,
    updateSettings: settings.updateSettings,

    exportPDF: exportApi.exportPDF,

    getDataPaths: dataApi.getPaths,
    createBackup: dataApi.createBackup,
    exportDataJson: dataApi.exportDataJson,
    getOrphanImages: dataApi.getOrphanImages,
    cleanupOrphanImages: dataApi.cleanupOrphanImages,

    getHealth: misc.getHealth,
    uploadImage: misc.uploadImage,
    deleteImage: misc.deleteImage,
  };

  global.API = API;
  if (typeof module !== 'undefined' && module.exports) {
    module.exports = API; // 便于 Node 下做自检
  }
})(typeof globalThis !== 'undefined' ? globalThis : this);
