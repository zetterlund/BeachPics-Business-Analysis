# BeachPics-Business-Analysis

## Scraper Overview

This is a scraper I built to download images from a photography website, to be used for training a neural network to do some computer vision tasks.

It uses multiple threads to each run instances of Selenium with ChromeDriver, which download images and save data to a MongoDB database.

MongoDB:
- 'beach_pics' database
	- 'surnames' collection
	- 'profiles' collection
	- 'profile_images' collection

When the script runs:
1) Connection to MongoDB is initialized with a call to MongoClient().
1) Local file 'name_list.json' contains a list of the top 1000 most common American surnames.  'NAME_COUNT' *(default: 50)* number of names are selected from this list, and each name is added as an empty document to the Mongo 'surnames' collection.
1) 'NUM_THREADS' *(default: 3)* number of threads are instantiated, started, and joined so that the program waits for all threads to finish before terminating.  Each thread runs the "run_scraper" function.  The "run_scraper" function consists of these sequential steps:
	- Instantiate Selenium ChromeDriver headless browser.
	- Iterate through all newly-added surnames, visiting the search results page for that surname, and for each search result, adding a document to the 'profiles' collection.
	- Iterate through all newly-added profiles, visiting the profile page.  Profile meta data is captured and 'profile' document is updated.  Profile image meta data is captured and stored as a document in the 'profile_images' collection.
	- Iterate through all un-scraped profiles.  All images for the profile are downloaded and saved to disk as three copies, each with different pre-defined image dimensions (Large, Small, Thumbnail).  Image meta data in the profile's 'profile_images' document is updated.
	- Close ChromeDriver.