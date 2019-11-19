import hashlib
import json
import logging
import os
import random
import re
import requests
import threading
import time
from pymongo import MongoClient
from selenium import webdriver
from selenium.webdriver.chrome.options import Options


# Set up logging
file_handler = logging.FileHandler('debug.log')
stream_handler = logging.StreamHandler()
stream_handler.setLevel(logging.INFO)
logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s:%(name)s:%(levelname)s:%(threadName)s:%(funcName)s - %(message)s',
                    handlers=(file_handler, stream_handler)
                    )


# Initialize random seeds
random.seed(None)
md5 = hashlib.md5()


# Set global constants
NAME_COUNT = 50
PROFILE_IMAGES_COUNT = 100
NUM_THREADS = 3



def populate_db_with_surnames():
    # Retrieve list of top 1000 surnames from local document
    with open('name_list.json', 'r') as the_file:
        name_list = json.load(the_file)
    name_list = [str(x).lower() for x in name_list]
    logging.info("{} names found in name_list".format(len(name_list)))

    # Add blank document to surname collection for each name in local list that is not in the database
    names_added = 0
    for name in name_list:
        p = db.surnames.find_one({'name': name})
        if not p:
            logging.info("Name {} not found in database; now inserting.".format(name))
            db.surnames.insert_one({'name': name, 'scraped': False})
            names_added += 1
            if names_added >= NAME_COUNT:
                break
        else:
            logging.info("Name {} already found in database; skipping.".format(name))

    logging.info("Finished adding names.  {} names added in this script run.".format(names_added))



def scrape_surname_page(driver, surname_document):

    try:
        surname = surname_document['name']

        search_url = 'https://777portraits.smugmug.com/search/?n=777portraits&scope=node&scopeValue=xs5mj&c=galleries&q={}'.format(surname)
        driver.get(search_url)
        time.sleep(5)

        family_list = driver.find_elements_by_xpath('//div[@class="sm-search-resultset"]/ul[contains(@class, "sm-search-tiles")]/li')
        logging.info("{} families found for surname {}.".format(len(family_list), surname_document['name']))

        for i, family in enumerate(family_list):
            try:
                p = dict()

                # Initiate blank variables now, which will be filled in later
                p['photo_count_on_site'] = 0
                p['photo_info_scraped'] = False
                p['photos_scraped'] = False

                # Grab data from page
                p['surname'] = surname
                p['search_url'] = driver.current_url
                p['data_clientid'] = family.find_element_by_xpath('./div').get_attribute('data-clientid')
                p['data_url'] = family.find_element_by_xpath('./div').get_attribute('data-url')
                p['name'] = family.find_element_by_xpath('.//div[@class="sm-search-tile-info"]//p').text
                p['profile_pic_url'] = family.find_element_by_xpath('.//div[contains(@class, "sm-search-tile")]//a').get_attribute('href')
                p['profile_pic_style'] = family.find_element_by_xpath('.//div[contains(@class, "sm-search-tile")]//a').get_attribute('style')
                p['result_page_placement'] = i+1

                # Determine if the profile is locked by the site, so we don't bother trying to scrape it later
                if p['profile_pic_style'] == 'background-image: url("");':
                    p['account_locked'] = True
                else:
                    p['account_locked'] = False

                # Generate document ID from the object's hash
                md5.update(str(p).encode('utf-8'))
                id_hash = "P" + str(md5.hexdigest()).upper()[:15]
                p['_id'] = id_hash

                with mongo_lock:
                    # Insert document into collection
                    db.profiles.insert_one(p)

                    # Insert blank document into image collection
                    db.profile_images.insert_one({'_id': p['_id'], 'images': []})

            except Exception as e:
                logging.error('Error encountered in "scrape_surname_page" function: {}'.format(e))

    except Exception as e:
        logging.error('Error encountered in top-level try/except of "scrape_surname_page" function for family "{}"": {}'.format(surname_document['name'], e))

    finally:
        with mongo_lock:
            # Mark surname as having had family info scraped
            db.surnames.update_one({'_id': surname_document['_id']}, {'$set': {'scraped': True}}, upsert=False)



def get_profile_images_info(driver, p):

    try:
        driver.get(p['data_url'])
        time.sleep(2)

        # Check if gallery is locked.  If so, mark as locked and continue the loop.
        try:
            element = driver.find_element_by_xpath('//h1[contains(text(), "Unlock Gallery")]')
            with mongo_lock:
                db.profiles.update_one({'_id': p['_id']}, {'$set': {'account_locked': True}}, upsert=False)
                return
        except:
            pass

        albumId = str(re.findall(r'(?<=\"albumId\":)([\d]+)', driver.page_source, re.DOTALL)[0])
        albumKey = str(re.findall(r'(?<=\"albumKey\":\")(.*?)\"', driver.page_source, re.DOTALL)[0])

        query_parameters = {
            'galleryType': 'album',
            'albumId': albumId,
            'albumKey': albumKey,
            'PageNumber': 1,
            'returnModelList': 'true',
            'PageSize': 500,
            'method': 'rpc.gallery.getalbum'
        }
        r = requests.get('https://777portraits.smugmug.com/services/api/json/1.4.0/', params=query_parameters).json()

        # Update the profile's document
        update_document = {
            'photo_count_on_site': r['Pagination']['TotalItems']
        }
        with mongo_lock:
            # Update the document in the db
            db.profiles.update_one({'_id': p['_id']}, {'$set': update_document}, upsert=False)

            # Update the document for the profile images
            db.profile_images.update_one({'_id': p['_id']}, {'$set': {'images': r['Images']}})

    except Exception as e:
        logging.error('Encountered exception in "get_profile_images_info" for ID {}: {}'.format(p['_id'], e))

    finally:
        with mongo_lock:
            # Mark profile as having had photo info scraped
            db.profiles.update_one({'_id': p['_id']}, {'$set': {'photo_info_scraped': True}}, upsert=False)



def download_profile_images(driver, p):

    try:
        with mongo_lock:
            images = db.profile_images.find_one({'_id': p['_id']})['images']

        image_indices = list(range(len(images)))
        random.shuffle(image_indices)

        image_download_count = 0
        for index in image_indices:
            image = images[index]

            if image.get('downloaded', None):
                continue

            try:
                # Download image in all sizes
                for size in ['X2', 'M', 'Th']:
                    try:
                        photo_url = 'https://photos.smugmug.com/photos/i-{}/0/{}/i-{}-{}.jpg'.format(image['ImageKey'],
                                                                                                     size,
                                                                                                     image['ImageKey'],
                                                                                                     size)
                        r = requests.get(photo_url, allow_redirects=True)

                        with os_lock:
                            photo_directory = os.path.join(os.getcwd(), 'photos', p['_id'], size)
                            if not os.path.exists(photo_directory):
                                os.makedirs(photo_directory)
                            with open(os.path.join(photo_directory, (str(image['ImageID'])+'.jpg')), 'wb') as photo_file:
                                photo_file.write(r.content)
                        time.sleep(0.5)

                    except Exception as e:
                        logging.error("Ran into error downloading photo for ImageID {} in size {}: {}".format(image['ImageID'], size, e))

                # Mark image as downloaded and overwrite existing entry in 'images' list
                image['downloaded'] = True
                images[index] = image

                image_download_count += 1
                if image_download_count >= PROFILE_IMAGES_COUNT:
                    break

            except Exception as e:
                logging.error('Encountered error while downloading image ID {}: {}'.format(image['ImageID'], e))

        logging.info("Finished gathering images for ID {}.  Retrieved {} images.".format(p['_id'], image_download_count))

    except Exception as e:
        logging.error('Encountered exception in top-level try/except of "download_profile_images" for ID {}: {}'.format(p['_id'], e))

    finally:
        with mongo_lock:
            # Mark profile as having had photos downloaded
            db.profiles.update_one({'_id': p['_id']}, {'$set': {'photos_scraped': True}}, upsert=False)



def run_scraper():

    logging.info('Beginning "run_scraper" function.')

    # Instantiate Selenium webdriver
    chrome_options = Options()
    chrome_options.add_argument("--headless") # Keep this to ensure Chrome runs properly
    chrome_options.add_argument('--user-agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/71.0.3578.98 Safari/537.36"')
    driver = webdriver.Chrome(executable_path=os.path.abspath("/home/sven/scrape/chromedriver237"), options=chrome_options)
    time.sleep(2)

    try:
        # Get list of profiles from each surname's search results page
        while True:
            with mongo_lock:
                p = db.surnames.find_one({'scraped': False})
                if not p:
                    break
                db.surnames.update_one({'_id': p['_id']}, {'$set': {'scraped': 'working'}}, upsert=False)
            logging.info("Now attempting to scrape surname page for name {}".format(p['name']))
            scrape_surname_page(driver, p)

        # Get profile images info for each profile
        while True:
            with mongo_lock:
                p = db.profiles.find_one({'account_locked': False, 'photo_info_scraped': False, 'photos_scraped': False})
                if not p:
                    break
                db.profiles.update_one({'_id': p['_id']}, {'$set': {'photo_info_scraped': 'working'}}, upsert=False)
            logging.info("Now attempting to get profile images info for profile ID {}".format(p['_id']))
            get_profile_images_info(driver, p)

        # Download profile images for each profile
        while True:
            with mongo_lock:
                p = db.profiles.find_one({'account_locked': False, 'photo_info_scraped': True, 'photos_scraped': False})
                if not p:
                    break
                db.profiles.update_one({'_id': p['_id']}, {'$set': {'photos_scraped': 'working'}}, upsert=False)
            logging.info("Now attempting to download profile images for profile ID: {}".format(p['_id']))
            download_profile_images(driver, p)

        logging.info('Finished thread: {}'.format(threading.current_thread().name))

    except Exception as e:
        logging.error('Failure in "run_scraper" function!  Thread: {}  Error: {}'.format(threading.current_thread().name, e))

    finally:
        driver.close()



if __name__ == '__main__':

    try:
        # Connect to MongoDB
        client = MongoClient()
        db = client['beach_pics']

        # Define multithreading locks
        mongo_lock = threading.Lock()
        os_lock = threading.Lock()

        # Set up surnames in database
        populate_db_with_surnames()


        # Instantiate threads
        threads = list()
        for x in range(NUM_THREADS):
            threads.append(threading.Thread(target=run_scraper))

        # Start threads
        for x in threads:
            x.start()
            time.sleep(3)

        # Wait until threads are completely executed
        for x in threads:
            x.join()

        # All threads have now completely executed
        logging.info("Done!")


    except Exception as e:
        logging.error("Top-level error found: {}".format(e))

    finally:
        logging.info("FINISHED WITH THIS RUN.\n\n\n\n\n")
