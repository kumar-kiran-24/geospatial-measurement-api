import requests
url = 'http://127.0.0.1:8002/api/files/'
files = {'file': open('C:/project-Geospatial/test_sample.kml', 'rb')}
r = requests.post(url, files=files)
print(r.status_code)
print(r.text)
