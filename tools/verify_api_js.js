/**
 * api.js 自检：在 Node 里加载 app/static/js/api.js，打到**真实运行的后端**，
 * 逐个核对路径、查询参数、请求体与返回结构。
 *
 * 用法：node tools/verify_api_js.js <baseUrl>
 * 退出码：0 全通过 / 1 有失败
 *
 * 为什么打真后端而不是 mock fetch：mock 只能证明"我调用了我以为的路径"，
 * 证明不了后端认这些路径与字段。这里要验证的是 api.js 与后端的契约一致。
 */

'use strict';

const BASE = process.argv[2] || 'http://127.0.0.1:8931';
const API = require('../app/static/js/api.js');

// Node 下没有页面同源上下文，需要显式指定基址
API.configure({ baseUrl: BASE });

let pass = 0;
const failures = [];

function check(name, ok, detail) {
  if (ok) {
    pass += 1;
    console.log(`[PASS] ${name}${detail ? ' — ' + detail : ''}`);
  } else {
    failures.push(`${name}${detail ? ' — ' + detail : ''}`);
    console.log(`[FAIL] ${name}${detail ? ' — ' + detail : ''}`);
  }
}

async function expectApiError(name, fn, wantStatus) {
  try {
    await fn();
    check(name, false, '没有抛异常');
  } catch (err) {
    const okType = err instanceof API.ApiError;
    const okStatus = wantStatus === undefined || err.status === wantStatus;
    check(name, okType && okStatus,
      `${err.name} status=${err.status} msg=${String(err.message).slice(0, 60)}`);
  }
}

(async function main() {
  console.log('='.repeat(78));
  console.log('api.js 自检（对真实后端）');
  console.log('='.repeat(78));

  // ---------- 基础：健康检查 ----------
  console.log('\n-- 基础与错误处理 --');
  const health = await API.getHealth();
  check('getHealth 返回 status', health && health.status === 'ok',
    JSON.stringify(health).slice(0, 80));

  await expectApiError('404 抛 ApiError（取不存在的题目）',
    () => API.getQuestion(999999), 404);
  await expectApiError('422 抛 ApiError（folder_id=null）',
    () => API.createQuestion({ folder_id: null }), 422);
  await expectApiError(
    '网络失败抛 ApiError(status=0)',
    () => API.request('http://127.0.0.1:1/nope'),
    0,
  );

  // 422 消息应包含字段名，便于直接展示给用户
  try {
    await API.updateQuestion(1, { is_starred: null });
  } catch (err) {
    check('422 错误消息含字段名（可直接展示）',
      /is_starred/.test(err.message), err.message.slice(0, 70));
  }

  // ---------- 文件夹 ----------
  console.log('\n-- 文件夹 --');
  const subject = await API.createFolder({ name: '高等数学' });
  check('createFolder 返回 id/level', subject.id > 0 && subject.level === 1,
    `id=${subject.id} level=${subject.level}`);

  const category = await API.createFolder({ name: '极限', parent_id: subject.id });
  check('createFolder 建大类 level=2', category.level === 2, `level=${category.level}`);

  const tree = await API.getFolderTree();
  check('getFolderTree 返回数组且含 children', Array.isArray(tree) &&
    Array.isArray(tree.find((n) => n.id === subject.id).children));
  check('getFolderTree 含 question_count',
    typeof tree.find((n) => n.id === subject.id).question_count === 'number');

  await expectApiError('updateFolder name=null -> 422',
    () => API.updateFolder(category.id, { name: null }), 422);
  const renamed = await API.updateFolder(category.id, { name: '极限与连续' });
  check('updateFolder 改名生效', renamed.name === '极限与连续', renamed.name);

  // ---------- 标签 ----------
  console.log('\n-- 标签 --');
  const q1 = await API.createQuestion({
    folder_id: category.id,
    stem: '求极限',
    answer: '洛必达',
    tags: ['极限', '洛必达'],
  });
  check('createQuestion 返回 tags 数组', Array.isArray(q1.tags) && q1.tags.length === 2,
    JSON.stringify(q1.tags));

  const q2 = await API.createQuestion({
    folder_id: category.id, stem: '纯图片题', tags: ['导数'],
  });
  check('stem/answer 可空（纯图片题）',
    q2.answer === null || q2.answer === undefined, `answer=${q2.answer}`);

  const allTags = await API.getTags();
  check('getTags 含 question_count', allTags.every((t) => 'question_count' in t),
    JSON.stringify(allTags.map((t) => [t.name, t.question_count])));
  check('getTags 高频在前',
    allTags.length < 2 || allTags[0].question_count >= allTags[1].question_count);

  const found = await API.searchTags('极');
  check('searchTags 命中「极限」', found.some((t) => t.name === '极限'),
    JSON.stringify(found.map((t) => t.name)));
  const fullWidth = await API.searchTags('ＡＢＣ');
  check('searchTags 全角输入不报错', Array.isArray(fullWidth));
  const limited = await API.searchTags('', 1);
  check('searchTags 传 limit 生效', limited.length <= 1, `${limited.length} 条`);

  // ---------- 题目筛选（含数组参数序列化） ----------
  console.log('\n-- 题目筛选（tag 数组序列化） --');
  const byKeyword = await API.getQuestions({ keyword: '求极限' });
  check('getQuestions keyword 命中', byKeyword.length === 1, `${byKeyword.length} 条`);

  const byTag = await API.getQuestions({ tag: ['极限'] });
  check('getQuestions 单标签命中',
    byTag.length === 1 && byTag[0].id === q1.id, `${byTag.length} 条`);

  const byTwoAnd = await API.getQuestions({ tag: ['极限', '洛必达'], tag_mode: 'and' });
  check('tag 数组按重复 key 传参 + and 生效',
    byTwoAnd.length === 1 && byTwoAnd[0].id === q1.id, `${byTwoAnd.length} 条`);

  const byTwoOr = await API.getQuestions({
    tag: ['极限', '导数'], tag_mode: 'or',
  });
  check('or 比 and 结果更宽', byTwoOr.length === 2, `${byTwoOr.length} 条`);

  const byStarred = await API.getQuestions({ starred: false });
  check('getQuestions starred=false 生效', byStarred.length === 2,
    `${byStarred.length} 条`);
  const byMastery = await API.getQuestions({ mastery: 'still_wrong' });
  check('getQuestions mastery 生效', byMastery.length === 2, `${byMastery.length} 条`);
  const byFolder = await API.getQuestions({ folder_id: category.id });
  check('getQuestions folder_id 生效', byFolder.length === 2, `${byFolder.length} 条`);
  check('列表项不含 deleted_at（与详情区分）',
    !('deleted_at' in byFolder[0]), Object.keys(byFolder[0]).join(','));

  // ---------- 题目编辑（PUT 的 null 语义） ----------
  console.log('\n-- 题目编辑（PUT null 语义） --');
  const cleared = await API.updateQuestion(q1.id, { answer: null });
  check('updateQuestion answer=null 清空', cleared.answer === null,
    `answer=${cleared.answer}`);
  check('未传的 stem 保持原值', cleared.stem === '求极限', cleared.stem);

  await expectApiError('updateQuestion is_starred=null -> 422',
    () => API.updateQuestion(q1.id, { is_starred: null }), 422);
  await expectApiError('updateQuestion folder_id=null -> 422',
    () => API.updateQuestion(q1.id, { folder_id: null }), 422);

  // undefined 必须被丢弃（未传），而不是变成 null
  const undefinedIgnored = await API.updateQuestion(q1.id,
    { stem: '求极限（改）', answer: undefined });
  check('body 里 undefined 被丢弃（answer 不被清空）',
    undefinedIgnored.answer === null && undefinedIgnored.stem === '求极限（改）',
    `stem=${undefinedIgnored.stem} answer=${undefinedIgnored.answer}`);

  const tagsCleared = await API.updateQuestion(q1.id, { tags: [] });
  check('updateQuestion tags=[] 清空标签', tagsCleared.tags.length === 0,
    `${tagsCleared.tags.length} 个`);

  // ---------- 星标 / 正误 ----------
  console.log('\n-- 星标与正误 --');
  const starred = await API.toggleStar(q1.id, true);
  check('toggleStar(true) -> is_starred', starred.is_starred === true);
  const unstarred = await API.toggleStar(q1.id, false);
  check('toggleStar(false) -> 取消', unstarred.is_starred === false);
  const toggled = await API.toggleStar(q1.id);
  check('toggleStar() 省略参数按当前值取反', toggled.is_starred === true,
    `is_starred=${toggled.is_starred}`);
  await API.toggleStar(q1.id, false);

  const masterySet = await API.toggleMastery(q1.id, 'mastered');
  check('toggleMastery 传值直接设置', masterySet.mastery_status === 'mastered',
    masterySet.mastery_status);
  const masteryToggled = await API.toggleMastery(q1.id);
  check('toggleMastery 不传值翻转', masteryToggled.mastery_status === 'still_wrong',
    masteryToggled.mastery_status);

  // ---------- 复习 ----------
  console.log('\n-- 复习 --');
  const count0 = await API.getReviewCount();
  check('getReviewCount 返回 { count }', typeof count0.count === 'number',
    JSON.stringify(count0));

  const checked = await API.checkReview(q1.id);
  check('checkReview 返回 next_review_at', !!checked.next_review_at,
    checked.next_review_at);
  check('打勾后 interval_index 归零', checked.interval_index === 0,
    `${checked.interval_index}`);
  const checkedWithMastery = await API.checkReview(q1.id, 2);
  check('checkReview 传 mastery 生效', checkedWithMastery.mastery_level === 2,
    `mastery_level=${checkedWithMastery.mastery_level}`);
  check('允许重复打勾（不 409）', checkedWithMastery.review_count >= 2,
    `review_count=${checkedWithMastery.review_count}`);

  const undone = await API.uncheckReview(q1.id);
  check('uncheckReview 返回 ok', undone.ok === true, JSON.stringify(undone));
  // 撤消失效时的状态码是 409（不是 404）：这里没有"找不到的资源"，
  // 而是"当前状态不允许该操作"。q1 建题时有一条初始记录，加上两次打勾共 3 条，
  // 因此最多能撤销 3 次，第 4 次才报错。
  const undone2 = await API.uncheckReview(q1.id);
  check('可连续撤销（第二次仍成功）', undone2.ok === true);
  const undone3 = await API.uncheckReview(q1.id);
  check('可撤销建题时的初始记录（第三次仍成功）', undone3.ok === true);
  await expectApiError('无记录可撤销 -> 409',
    () => API.uncheckReview(q1.id), 409);

  const today = await API.getReviewToday();
  check('getReviewToday 返回数组', Array.isArray(today), `${today.length} 条`);
  check('今日队列项含约定的派生字段',
    today.length === 0 ||
    ['overdue_days', 'is_backlog', 'is_overdue'].every((k) => k in today[0]),
    today.length ? Object.keys(today[0]).join(',') : '(空队列，跳过)');

  const stats = await API.getBackfillStats();
  check('getBackfillStats 三个字段',
    ['today_backfill_count', 'consecutive_days', 'backlog_count'].every(
      (k) => k in stats), JSON.stringify(stats));

  const reset = await API.resetBackfill(false);
  check('resetBackfill 返回 affected_count', 'affected_count' in reset,
    JSON.stringify(reset).slice(0, 80));

  // 无积压题时也要能看出生效窗口（曾经因为提前返回而漏算，见 review_service）
  const resetSpreadEmpty = await API.resetBackfill(true, 7);
  check('无积压题时 spread_days 仍反映生效窗口',
    resetSpreadEmpty.mode === 'spread' && resetSpreadEmpty.spread_days === 7,
    `mode=${resetSpreadEmpty.mode} days=${resetSpreadEmpty.spread_days}`);
  const resetSpreadDefault = await API.resetBackfill(true);
  check('spread 未传 days 时回退 settings.backfill_reset_days',
    resetSpreadDefault.spread_days === 14,
    `days=${resetSpreadDefault.spread_days}`);

  const resetAll = await API.resetBackfill(false);
  check('非分散模式 spread_days 为 null',
    resetAll.spread_days === null, `days=${resetAll.spread_days}`);

  // ---------- 记事本 ----------
  console.log('\n-- 记事本 --');
  const note = await API.createNote({ title: '极限笔记', content: '洛必达适用条件' });
  check('createNote 返回 id', note.id > 0, `id=${note.id}`);

  const notes = await API.getNotes();
  check('getNotes 按 updated_at 倒序',
    notes.length < 2 || notes[0].updated_at >= notes[1].updated_at);

  const noteDetail = await API.getNote(note.id);
  check('getNote 返回完整 content', noteDetail.content === '洛必达适用条件');

  const noteUpdated = await API.updateNote(note.id, { title: '极限笔记（改）' });
  check('updateNote 只改传入字段', noteUpdated.title === '极限笔记（改）' &&
    noteUpdated.content === '洛必达适用条件');
  const noteCleared = await API.updateNote(note.id, { content: null });
  check('updateNote content=null 清空', noteCleared.content === null);

  const noteSearch = await API.searchNotes('极限');
  check('searchNotes 命中标题', noteSearch.some((n) => n.id === note.id),
    `${noteSearch.length} 条`);
  const noteSearchEmpty = await API.searchNotes('');
  check('searchNotes 空词返回空数组', Array.isArray(noteSearchEmpty) &&
    noteSearchEmpty.length === 0);

  // ---------- 设置 ----------
  console.log('\n-- 设置 --');
  const settings = await API.getSettings();
  check('getSettings 三个配置项',
    ['intervals', 'backfill_limit', 'backfill_reset_days'].every(
      (k) => k in settings), JSON.stringify(settings));
  check('intervals 是数组（不是字符串）', Array.isArray(settings.intervals),
    typeof settings.intervals);

  const settingsUpdated = await API.updateSettings({ intervals: [5, 10, 20, 40] });
  check('updateSettings 更新 intervals',
    JSON.stringify(settingsUpdated.intervals) === '[5,10,20,40]',
    JSON.stringify(settingsUpdated.intervals));
  check('未传字段保持原值',
    settingsUpdated.backfill_limit === settings.backfill_limit,
    `backfill_limit=${settingsUpdated.backfill_limit}`);

  const settingsReset = await API.updateSettings({ intervals: null });
  check('updateSettings 传 null 回退默认',
    JSON.stringify(settingsReset.intervals) === '[3,7,15,30]',
    JSON.stringify(settingsReset.intervals));

  await expectApiError('updateSettings 非法 intervals -> 422',
    () => API.updateSettings({ intervals: [3, 7, 7, 30] }), 422);

  // ---------- 删除 ----------
  console.log('\n-- 删除 --');
  await expectApiError('deleteFolder 有题目时 -> 409',
    () => API.deleteFolder(category.id), 409);
  const deletedQ = await API.deleteQuestion(q1.id);
  check('deleteQuestion 返回 ok', deletedQ.ok === true);
  await expectApiError('删除后再取 -> 404', () => API.getQuestion(q1.id), 404);

  const deletedNote = await API.deleteNote(note.id);
  check('deleteNote 返回 ok', deletedNote.ok === true);
  await expectApiError('删除后再取笔记 -> 404', () => API.getNote(note.id), 404);

  const deletedFolder = await API.deleteFolder(subject.id, true);
  check('deleteFolder force=true 成功', deletedFolder.ok === true);

  // ---------- exportPDF：后端未实现，应抛 ApiError 而非静默失败 ----------
  console.log('\n-- exportPDF（后端尚未实现） --');
  await expectApiError('exportPDF 未实现时抛 ApiError（不是静默 resolve）',
    () => API.exportPDF({ scope: 'all' }));

  console.log('\n' + '-'.repeat(78));
  console.log(`合计 ${pass + failures.length} 项，通过 ${pass}，失败 ${failures.length}`);
  failures.forEach((f) => console.log('  FAILED: ' + f));
  process.exit(failures.length ? 1 : 0);
})().catch((err) => {
  console.error('自检自身异常:', err && err.stack ? err.stack : err);
  process.exit(2);
});
