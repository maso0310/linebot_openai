import requests
import webbrowser
from lxml import etree
from bs4 import BeautifulSoup



def get_image_urls(search_query, num_images=10):
    # 構造 Google 圖片搜尋的 URL
    url = f"https://www.google.com/search?q={search_query}&tbm=isch"
    # 偽裝為瀏覽器發送請求，避免被阻擋
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/58.0.3029.110 Safari/537.3"}
    
    # 發送請求並獲取回應內容
    response = requests.get(url, headers=headers)
    response.raise_for_status()
    
    # 确保请求成功
    if response.status_code == 200:
        # 将HTML内容写入临时文件
        with open('temp.html', 'w', encoding='utf-8') as f:
            f.write(response.text)

        # 用默认的浏览器打开临时文件
        webbrowser.open('temp.html')

    # 解析 HTML 頁面
    soup = BeautifulSoup(response.content, "html.parser")
    
    
    
    
    _elements = soup.find_all("div",  attrs={'data-id': 'QTES7d06VDbb0M'})
    
    # 如果找到了對應的元素
    if _elements:
        for div in _elements:
            data_id = div["data-id"]
            print("data-id:", data_id)
    else:
        print("找不到指定的目標元素。")
'''
    # 發送請求並獲取回應內容
    response = requests.get(url, headers=headers)
    response.raise_for_status()

    # 解析 HTML 頁面
    soup = BeautifulSoup(response.content, "html.parser")
    # 使用XPath進行查找
    time.sleep(0.5)
    element = soup.find(xpath="/html/body/div[2]/c-wiz/div[3]/div[1]/div/div/div/div/div[1]/div[1]/span/div[1]/div[1]/div[1]")

    # 如果找到了對應的元素
    if element:
        # 獲取data-id屬性的值
        data_id = element.get("data-id")
        print("data-id:", data_id)
    else:
        print("找不到指定的目標元素。")
'''
'''


        # 儲存圖片 URL 的列表
        image_urls = []
        # 遍歷所有 <img> 標籤
        for img in soup.find_all("img"):
            # 如果已經獲取到指定數量的圖片 URL，則停止遍歷
            if len(image_urls) >= num_images:
                break
            # 提取圖片的 URL
            img_url = img.get("src")
            # 確保 URL 是有效的以 http 開頭
            if img_url and img_url.startswith("http"):
                # 將圖片 URL 加入列表
                image_urls.append(img_url)

    # 返回圖片 URL 的列表
    return image_urls'''

if __name__ == "__main__":
    # 設定搜尋關鍵字和要抓取的圖片數量
    search_query = input("請輸入梗圖名稱")+" 梗圖"#"請支援收銀"
    num_images = 5
    get_image_urls(search_query,num_images)
    # 獲取圖片 URL 的列表
    #image_urls = get_image_urls(search_query, num_images)
    
    # 列印出每個圖片的 URL
    #for idx, img_url in enumerate(image_urls, start=1):
    #    print(f"Image {idx}: {img_url}")
