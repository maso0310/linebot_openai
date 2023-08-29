from icrawler.downloader import ImageDownloader
from icrawler.builtin import GoogleImageCrawler
from icrawler.utils import Session
import img_check
from youtubesearchpython import VideosSearch
import requests

max_num=5
class CustomLinkPrinter(ImageDownloader):
    file_urls = []
    def get_filename(self, task, default_ext):
        file_idx = self.fetched_num + self.file_idx_offset
        return '{:04d}.{}'.format(file_idx, default_ext)

    def download(self, task, default_ext, timeout=5, max_retry=3, overwrite=False, **kwargs):
        self.session.headers['Cache-Control'] = 'no-store'
        file_url = task['file_url']
        filename = self.get_filename(task, default_ext)

        task['success'] = True
        task['filename'] = filename

        if not self.signal.get('reach_max_num'):
            self.file_urls.append(file_url)

        self.fetched_num += 1

        if self.reach_max_num():
            self.signal.set(reach_max_num=True)

        return


init_params = {
    'downloader_cls': CustomLinkPrinter,
    # 添加其他参数
}
def get_img_url(keyword="yee"):
    CustomLinkPrinter.file_urls = []  # 清空圖片鏈接列表
    google_crawler = GoogleImageCrawler( **init_params)
    google_crawler.crawl(keyword=keyword+" 梗圖", max_num=max_num)  # 根据需要调整参数

    file_urls = google_crawler.downloader.file_urls
    rtn=""
    for i in range(max_num):
        if file_urls:
            if(file_urls[i].count('http')>1):
                print("原本:",file_urls[i])
                file_urls[i] = "http"+file_urls[i].rsplit('http', 1)[-1]
            rtn =file_urls[i]
            if img_check.check_img_url(rtn):
                print("result in request:",rtn)
                return rtn
            elif i==max_num-1:
                rtn ="https://img.onl/C2QbRo"
        else:#如果根本沒搜到
            rtn ="https://img.onl/C2QbRo"
            return rtn

def find_video(keyword):
    videos_search = VideosSearch(keyword+" 歌", limit = 1)  # 搜尋關鍵字並限制結果數量為 1
    result = videos_search.result()
    
    if result['result']:
        first_video = result['result'][0]
        video_url = f"https://www.youtube.com/watch?v={first_video['id']}"
        return video_url
    else:
        return None


def check_keywords(text="柾國"):
    # GitHub raw URL of your keywords text file
    github_url = "https://watermelon-1234.github.io/bad_list/index.html"
    
    # Get the keywords from the GitHub text file
    response = requests.get(github_url)
    if response.status_code == 200:
        keywords = response.text.splitlines()
    else:
        print("Failed to fetch keywords from GitHub")
        return False

    # Check if the sample text contains any keyword
    for keyword in keywords:
        if keyword.lower() in text.lower():
            return True
    return False


#print(check_keywords())
#get_img_url("一定是廠商的疏失")#測試用