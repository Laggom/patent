import os
from playwright.sync_api import sync_playwright, expect

class PatentDownloader:
    def __init__(self, downloads_path="downloads"):
        """
        PatentDownloader 클래스의 생성자입니다.
        다운로드 폴더를 준비합니다.
        """
        self.downloads_path = downloads_path
        if not os.path.exists(self.downloads_path):
            os.makedirs(self.downloads_path)
            print(f"Created download directory at: {self.downloads_path}")

    def download_patent_pdf(self, **kwargs):
        """
        Playwright를 사용하여 특허를 검색하고 첫 번째 결과의 PDF를 다운로드합니다.
        키워드 인수를 통해 유연한 고급 검색을 지원합니다.
        예: download_patent_pdf(keyword="OLED", assignee="LG", cpc="H01L51/52")
        """

        query_parts = []
        for key, value in kwargs.items():
            if value:
                # 'keyword'는 특별히 처리, 나머지는 'key:("value")' 형식
                if key == 'keyword':
                    query_parts.append(str(value))
                else:
                    # 파이썬 키워드 인자에 하이픈(-)을 사용할 수 없으므로, 언더스코어(_)를 하이픈으로 변환
                    formatted_key = key.replace('_', '-')
                    query_parts.append(f'{formatted_key}:("{value}")')

        search_query = " ".join(query_parts)

        if not search_query:
            print("Error: No search criteria provided.")
            return None

        for attempt in range(3):
            try:
                with sync_playwright() as p:
                    browser = p.chromium.launch(headless=True)
                    page = browser.new_page()
                    print(f"--- Attempt {attempt + 1} of 3 ---")

                    print("Navigating to Google Patents...")
                    page.goto("https://patents.google.com/", timeout=60000)

                    print(f"Executing search with query: '{search_query}'")
                    search_input = page.locator("#searchInput")
                    search_input.fill(search_query)
                    search_input.press("Enter")

                    print("Waiting for search results to load...")
                    results_container = page.locator("#resultsContainer")
                    expect(results_container).to_be_visible(timeout=30000)

                    if page.locator("state-modifier.result-title").count() == 0:
                        raise Exception("No search results found for the given query")

                    print("Locating the first patent link...")
                    first_patent_link = page.locator("state-modifier.result-title").first
                    expect(first_patent_link).to_be_visible(timeout=10000)

                    print("Navigating to the patent page...")
                    first_patent_link.click()

                    page.wait_for_url("**/patent/**", timeout=30000)
                    print(f"Successfully navigated to patent page: {page.url}")

                    print("Attempting to download PDF...")
                    with page.expect_download() as download_info:
                        page.get_by_text("Download PDF").click()

                    download = download_info.value
                    file_path = os.path.join(self.downloads_path, download.suggested_filename)

                    print(f"Saving download to {file_path}...")
                    download.save_as(file_path)
                    print("PDF downloaded successfully.")
                    browser.close()
                    return file_path

            except Exception as e:
                print(f"An error occurred on attempt {attempt + 1}: {e}")
                if attempt < 2:
                    print("Retrying...")
                else:
                    print("All attempts failed.")
                    return None
        return None

if __name__ == '__main__':
    downloader = PatentDownloader()
    print("--- Patent Downloader script ---")
    print("This script is ready to use.")
    print("To run a download, uncomment one of the test cases below or add your own.")

    # --- 테스트 케이스 1: 키워드 + CPC ---
    # print("\n--- Running Test 1: Keyword + CPC ---")
    # test_1_params = {"keyword": "semiconductor", "cpc": "H01L29/78"}
    # file_1 = downloader.download_patent_pdf(**test_1_params)
    # if file_1:
    #     print(f"Test 1 successful. File saved at: {file_1}")
    # else:
    #     print("Test 1 failed.")

    # --- 테스트 케이스 2: 양수인 + 날짜 (2020년 이후 출원) ---
    # print("\n--- Running Test 2: Assignee + Date ---")
    # test_2_params = {"assignee": "Intel", "after_filing": "20200101"}
    # file_2 = downloader.download_patent_pdf(**test_2_params)
    # if file_2:
    #     print(f"Test 2 successful. File saved at: {file_2}")
    # else:
    #     print("Test 2 failed.")

    # --- 테스트 케이스 3: 발명가 + 특허 상태 (등록 특허) ---
    # print("\n--- Running Test 3: Inventor + Status ---")
    # test_3_params = {"inventor": "Carver Mead", "status": "grant"}
    # file_3 = downloader.download_patent_pdf(**test_3_params)
    # if file_3:
    #     print(f"Test 3 successful. File saved at: {file_3}")
    # else:
    #     print("Test 3 failed.")
