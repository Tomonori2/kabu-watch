/**
 * 株ウォッチ「みんなの声」受け口（Google Apps Script）
 *
 * できること
 *   ・アプリの意見箱から届いた文を、シート「意見」に1行ずつ書く（中身は公開しない）
 *   ・「つぎに作るもの」の投票を受けて、シート「投票」に書く（1人1候補1票）
 *   ・アプリが開かれたとき、候補と票の数・「直したこと」を返す
 *     ↑ これはあなたが書いたシート（候補・直したこと）だけ。みんなの意見の文は外に出さない
 *
 * はじめかた：gas/はじめかた.md を見てください
 */

var 最大文字 = 500;
var 一分の上限 = 30;   // 1分間に受ける数の上限（いたずらで大量に送られたとき用）

function setup() {
  var ss = SpreadsheetApp.getActive();
  シートを用意_(ss, '意見', ['届いた日時', '種類', '内容', 'アプリの版', '状態（未読/読んだ/直した）', 'メモ']);
  シートを用意_(ss, '投票', ['日時', '候補ID', '投票した端末（ランダムな番号）']);
  var 候補 = シートを用意_(ss, '候補', ['候補ID（英数字）', '題名', 'ひとこと説明', '状態（受付中/作った/見送り）']);
  if (候補.getLastRow() < 2) {
    候補.getRange(2, 1, 5, 4).setValues([
      ['chart', '株価と買い残のグラフ', 'カードを押すと、ここ数週間の動きが線で見られる', '受付中'],
      ['sort', '並べかえ', '値上がり順・買い残の多い順などで並べる', '受付中'],
      ['alert', '大きく動いたらお知らせ', '登録した銘柄が大きく動いた日に知らせる', '受付中'],
      ['kessan', '決算の日', '次の決算発表日をカードに出す', '受付中'],
      ['holding', '持ち株の損益', '買った値段と株数を入れると損益が出る（端末の中だけ）', '受付中']
    ]);
  }
  var 直 = シートを用意_(ss, '直したこと', ['日付', '直したこと（アプリにそのまま出ます）']);
  if (直.getLastRow() < 2) {
    直.getRange(2, 1, 1, 2).setValues([[Utilities.formatDate(new Date(), 'Asia/Tokyo', 'yyyy/MM/dd'), '公開しました。ご意見お待ちしています']]);
  }
  Logger.log('準備できました');
}

function シートを用意_(ss, 名前, 見出し) {
  var sh = ss.getSheetByName(名前) || ss.insertSheet(名前);
  if (sh.getLastRow() === 0) {
    sh.appendRow(見出し);
    sh.setFrozenRows(1);
  }
  return sh;
}

function 返す_(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}

// セルの先頭が = + - @ だと「式」として動いてしまうので、文字として入れる（式の注入対策）
function 安全な文字_(s, 長さ) {
  s = String(s == null ? '' : s).replace(/[\u0000-\u0008\u000B\u000C\u000E-\u001F]/g, '').slice(0, 長さ);
  if (/^[=+\-@\t\r]/.test(s)) s = "'" + s;
  return s;
}

function 多すぎ_() {
  var c = CacheService.getScriptCache();
  var key = 'n' + Math.floor(Date.now() / 60000);
  var n = Number(c.get(key) || 0) + 1;
  c.put(key, String(n), 120);
  return n > 一分の上限;
}

function 候補一覧_() {
  var sh = SpreadsheetApp.getActive().getSheetByName('候補');
  if (!sh || sh.getLastRow() < 2) return [];
  return sh.getRange(2, 1, sh.getLastRow() - 1, 4).getValues()
    .filter(function (r) { return /^[A-Za-z0-9_-]{1,30}$/.test(String(r[0])); })
    .map(function (r) { return { id: String(r[0]), t: String(r[1]), d: String(r[2]), s: String(r[3] || '受付中') }; });
}

function doGet() {
  var ss = SpreadsheetApp.getActive();
  var 票 = {};
  var 投票 = ss.getSheetByName('投票');
  if (投票 && 投票.getLastRow() > 1) {
    投票.getRange(2, 2, 投票.getLastRow() - 1, 1).getValues().forEach(function (r) {
      票[r[0]] = (票[r[0]] || 0) + 1;
    });
  }
  var 直 = [];
  var sh = ss.getSheetByName('直したこと');
  if (sh && sh.getLastRow() > 1) {
    直 = sh.getRange(2, 1, sh.getLastRow() - 1, 2).getValues()
      .filter(function (r) { return r[1]; })
      .map(function (r) {
        var d = r[0] instanceof Date ? Utilities.formatDate(r[0], 'Asia/Tokyo', 'yyyy/MM/dd') : String(r[0]);
        return { d: d, t: String(r[1]) };
      }).reverse().slice(0, 30);
  }
  var 意見 = ss.getSheetByName('意見');
  return 返す_({
    ok: true,
    candidates: 候補一覧_().map(function (c) { c.n = 票[c.id] || 0; return c; }),
    done: 直,
    count: 意見 ? Math.max(0, 意見.getLastRow() - 1) : 0   // 届いた意見の「数」だけ出す（中身は出さない）
  });
}

function doPost(e) {
  try {
    if (多すぎ_()) return 返す_({ ok: false, msg: '混み合っています。少したってから送ってください' });
    var b = JSON.parse(e.postData.contents);
    var lock = LockService.getScriptLock();
    lock.waitLock(10000);
    try {
      var ss = SpreadsheetApp.getActive();
      if (b.type === 'iken') {
        var 種類 = ['ほしい機能', 'まちがい・おかしい', '使いにくい', 'そのほか'].indexOf(b.kind) >= 0 ? b.kind : 'そのほか';
        var 内容 = 安全な文字_(b.text, 最大文字).trim();
        if (内容.length < 2) return 返す_({ ok: false, msg: '内容を書いてください' });
        ss.getSheetByName('意見').appendRow([new Date(), 種類, 内容, 安全な文字_(b.ver, 20), '未読', '']);
        return 返す_({ ok: true });
      }
      if (b.type === 'vote') {
        var id = String(b.id || '');
        var 端末 = String(b.voter || '');
        if (!/^[A-Za-z0-9]{8,40}$/.test(端末)) return 返す_({ ok: false, msg: '送れませんでした' });
        var c = 候補一覧_().filter(function (x) { return x.id === id; })[0];
        if (!c || c.s !== '受付中') return 返す_({ ok: false, msg: 'この候補は受付を終えました' });
        var sh = ss.getSheetByName('投票');
        if (sh.getLastRow() > 1) {
          var 済 = sh.getRange(2, 2, sh.getLastRow() - 1, 2).getValues().some(function (r) {
            return r[0] === id && r[1] === 端末;
          });
          if (済) return 返す_({ ok: true, already: true });
        }
        sh.appendRow([new Date(), id, 端末]);
        return 返す_({ ok: true });
      }
      return 返す_({ ok: false, msg: '送れませんでした' });
    } finally {
      lock.releaseLock();
    }
  } catch (err) {
    return 返す_({ ok: false, msg: '送れませんでした' });
  }
}
