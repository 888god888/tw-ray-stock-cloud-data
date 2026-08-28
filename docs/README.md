# 台股盤後行動選股 PWA

## 策略與成交量篩選

- 可依今日成交量張數區間、近 N 日平均量、今日相對均量倍數篩選。
- 可命名保存多套策略，之後載入、覆蓋或刪除。
- 策略保存在目前裝置的瀏覽器資料中；Android 與 iPhone 不會自動互相同步。

這個資料夾可直接部署為 GitHub Pages，iPhone 使用 Safari 開啟後，
可透過「分享 → 加入主畫面」安裝成獨立 Web App。

## 啟用 GitHub Pages

1. 將整個 `docs` 資料夾放進儲存庫的 `main` 分支。
2. 進入 GitHub 儲存庫的 `Settings → Pages`。
3. `Source` 選擇 `Deploy from a branch`。
4. Branch 選 `main`，資料夾選 `/docs`，按 `Save`。
5. 等待部署完成後，開啟：
   `https://888god888.github.io/tw-ray-stock-cloud-data/`

## 資料更新

PWA 會讀取固定 Release `latest` 中的：

- `manifest.json`：版本、日期、SHA-256。
- `snapshot.json.gz`：完整市場盤後快照。

股票快照會解壓後存進 IndexedDB；離線時仍可使用最近一次成功下載的資料。
App 程式外殼由 Service Worker 快取，股票快照不會放進 Service Worker Cache。

## 本機預覽

在儲存庫根目錄執行：

```bash
python -m http.server 8000 --directory docs
```

再開啟 `http://localhost:8000`。Service Worker 在 `localhost` 可正常註冊。
