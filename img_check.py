import urllib.request
def check_img_url(url):
    url = 'https://memeprod.sgp1.digitaloceanspaces.com/user-wtf/1646875056807.jpg'
    try:
        response = urllib.request.urlopen(url)
        if response.status == 200 and response.headers['Content-Type'].startswith('image/') and int(response.headers['Content-Length']) <= 10 * 1024 * 1024:
            print('The image link is correct and the image is less than 10GB.')
            return True
        else:
            print('The image link is correct but the image is not less than 10GB or not an image.')
            return False
    except urllib.error.HTTPError as errh:
        print("HTTP Error:", errh)
        return False
    except urllib.error.URLError as erru:
        print("URL Error:", erru)
        return False
    except ValueError as errv:
        print("Value Error:", errv)
        return False
    
print(check_img_url("123"))