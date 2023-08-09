from icrawler.downloader import ImageDownloader
from icrawler.builtin import GoogleImageCrawler

class CustomLinkPrinter(ImageDownloader):
    file_urls = []

    def get_filename(self, task, default_ext):
        file_idx = self.fetched_num + self.file_idx_offset
        return '{:04d}.{}'.format(file_idx, default_ext)

    def download(self, task, default_ext, timeout=5, max_retry=3, overwrite=False, **kwargs):
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
def get_img_url(keyword):
    google_crawler = GoogleImageCrawler(**init_params)
    google_crawler.crawl(keyword=keyword+" 梗圖", max_num=1)  # 根据需要调整参数

    file_urls = google_crawler.downloader.file_urls
    rtn=""
    if(file_urls[0]):
        rtn = file_urls[0]
    else:
        rtn = "https://memeprod.ap-south-1.linodeobjects.com/user-template/0f3ce1930440d817e8a477a175f871ed.png"
    print("result",rtn)
    return rtn