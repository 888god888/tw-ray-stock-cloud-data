# 台股盤後雲端資料

這個公開儲存庫每天在台北時間 18:00 由 GitHub Actions 更新 TWSE、TPEx 與
MOPS 官方盤後資料，並發布 Android App 使用的壓縮快照。

- 第一次執行：建立至少 180 個交易日的完整資料庫。
- 後續執行：下載前次 SQLite、差異更新，再發布新快照。
- 更新失敗：工作流程失敗，不覆蓋前一次 Release，手機仍可使用舊資料。
- 不含 FinMind Token、永豐 API Token 或任何私人帳號資料。
- EPS 優先由 MOPS 歷史財報換算單季值；GitHub 主機無法讀取歷史頁時，
  會使用 TWSE／TPEx「各產業 EPS 統計」補上最新官方累計值並清楚標示。

固定下載位置：`Releases / latest / snapshot.json.gz`。

## 第一次啟用

1. 把本專案全部檔案（包含 `.github`）放到儲存庫預設分支。
2. 開啟 GitHub 的 **Actions → 更新台股盤後資料 → Run workflow**。
3. 第一次會下載完整歷史資料，時間明顯比之後的差異更新久。
4. 成功後 Releases 會出現 `latest`；Android 1.1.0 即可按「更新最新資料」。

排程預設為台北時間週一至週五 18:00。若官方資料源暫時維護，該次工作會失敗
並保留上一個可用 Release，不會讓手機下載到半套資料。
## iPhone PWA

`docs/` 是不需要登入 ChatGPT 的獨立 iPhone PWA。GitHub Pages 啟用後，
可由 Safari 開啟並使用「分享 → 加入主畫面」安裝。

`.github/workflows/pages.yml` 會在盤後資料更新成功後，把最新快照與 PWA
一起部署到同一網域，避免 Safari 跨網域下載被 CORS 阻擋。

預定網址：`https://888god888.github.io/tw-ray-stock-cloud-data/`

詳細設定請看 [`docs/README.md`](docs/README.md)。
