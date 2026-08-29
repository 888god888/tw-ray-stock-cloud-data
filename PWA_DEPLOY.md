# iPhone PWA 更新失敗修正

Safari 的錯誤原因是舊版 PWA 直接下載 GitHub Release 檔案，重新導向後會被瀏覽器的跨網域（CORS）規則阻擋。

新版改成由 GitHub Pages Workflow 把 PWA 與最新盤後快照一起部署，兩者使用同一個網域。

## 一次性設定

1. 把修正版的 `docs` 資料夾上傳到儲存庫根目錄，取代舊版。
2. 把 `.github/workflows/pages.yml` 上傳到儲存庫相同位置。
3. 到 `Settings → Pages`，將 `Source` 選成 `GitHub Actions`。
4. 到 `Actions → 部署 iPhone PWA → Run workflow` 手動執行一次。
5. 等待工作顯示綠色勾勾，再用 Safari 開啟：
   `https://888god888.github.io/tw-ray-stock-cloud-data/`

可以先測試這個網址是否會顯示 JSON：

`https://888god888.github.io/tw-ray-stock-cloud-data/data/manifest.json`

## 之後如何更新

`更新台股盤後資料` 成功後，會自動啟動 `部署 iPhone PWA`，不需要在手機上另外操作。手機開啟時會檢查新版本，下載後保存在裝置內。

如果主畫面上的舊版仍顯示更新失敗，先完全關閉該 App 再開啟；仍未更新時，刪除舊的主畫面圖示，再從 Safari 重新「加入主畫面」。
