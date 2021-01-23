# goodreads_parser.py
'''Parses goodreads data depending on user settings'''
from __future__ import division

from future import standard_library
standard_library.install_aliases()
from builtins import str
from builtins import object
from past.utils import old_div
import re
import json
import base64
import zipfile
import datetime
import urllib.parse
from urllib.request import urlopen
from lxml import html

from calibre_plugins.xray_creator.config import __prefs__ as prefs
from calibre_plugins.xray_creator.lib.utilities import open_url, BOOK_ID_PAT, GOODREADS_ASIN_PAT, auto_expand_aliases

class GoodreadsParser(object):
    '''Parses Goodreads page for x-ray, author profile, start actions, and end actions as needed'''

    def __init__(self, url, connection, asin):
        self._connection = connection
        self._asin = asin

        book_id_search = BOOK_ID_PAT.search(url)
        self._goodreads_book_id = book_id_search.group(1) if book_id_search else None

        response = open_url(self._connection, url)
        self._page_source = None
        if not response:
            return
        self._page_source = html.fromstring(response)

        self._author_recommendations = None
        self._author_other_books = []

    def parse(self, create_xray=False, create_author_profile=False, create_start_actions=False, create_end_actions=False):
        '''Parses goodreads for x-ray, author profile, start actions, and end actions depending on user settings'''
        if self._page_source is None:
            return

        compiled_xray = self._get_xray() if create_xray else None
        non_xray_results = self._get_non_xray(create_author_profile, create_start_actions, create_end_actions)
        compiled_author_profile, compiled_start_actions, compiled_end_actions = non_xray_results

        return compiled_xray, compiled_author_profile, compiled_start_actions, compiled_end_actions

    def _get_xray(self):
        '''Gets x-ray data from goodreads and creates x-ray dict'''
        characters = self.get_characters(1)
        settings = self.get_settings(len(characters) + 1)
        quotes = self._get_quotes()
        return self._compile_xray(characters, settings, quotes)

    def _get_non_xray(self, create_author_profile, create_start_actions, create_end_actions):
        '''Gets and processes non-xray related data'''
        compiled_author_profile = None
        compiled_start_actions = None
        compiled_end_actions = None

        if not create_author_profile and not create_start_actions and not create_end_actions:
            return compiled_author_profile, compiled_start_actions, compiled_end_actions

        author_info = self._get_author_info()
        if not author_info:
            return compiled_author_profile, compiled_start_actions, compiled_end_actions
        self._read_primary_author_page(author_info)
        self._get_author_other_books(author_info)

        if create_author_profile:
            compiled_author_profile = self._compile_author_profile(author_info)

        if create_start_actions or create_end_actions:
            with zipfile.ZipFile(prefs['plugin_path'], 'r') as template_file:
                goodreads_templates = json.loads(template_file.read('templates/goodreads_data_template.json'))

            self._read_secondary_author_pages(author_info)
            book_image_url = self._get_book_image_url()

            if create_start_actions:
                reading_info = self._get_num_pages_and_reading_time()
                compiled_start_actions = self._compile_start_actions(goodreads_templates['BASE_START_ACTIONS'], author_info,
                                                                     reading_info, book_image_url)

            if create_end_actions:
                cust_recommendations = self._get_customer_recommendations()
                compiled_end_actions = self._compile_end_actions(goodreads_templates['BASE_END_ACTIONS'], author_info,
                                                                 cust_recommendations, book_image_url)

        return compiled_author_profile, compiled_start_actions, compiled_end_actions

    @staticmethod
    def _compile_xray(characters, settings, quotes):
        '''Compiles x-ray data into dict'''
        return {'characters': characters, 'settings': settings, 'quotes': quotes}

    def _compile_author_profile(self, author_info):
        '''Compiles author profile data into dict'''
        return {'u': [{'y': 277,
                       'l': [x['a'] for x in self._author_other_books],
                       'n': author_info[0]['name'],
                       'b': author_info[0]['bio'],
                       'i': author_info[0]['encoded_image']}],
                'd': int((datetime.datetime.now() - datetime.datetime(1970, 1, 1)).total_seconds()),
                'o': self._author_other_books,
                'a': self._asin
               }

    def _compile_start_actions(self, start_actions, author_info, reading_info, book_image_url):
        '''Compiles start actions data into dict'''
        timestamp = int((datetime.datetime.now() - datetime.datetime(1970, 1, 1)).total_seconds())

        start_actions['bookInfo']['asin'] = self._asin
        start_actions['bookInfo']['timestamp'] = timestamp
        start_actions['bookInfo']['imageUrl'] = book_image_url

        data = start_actions['data']

        for author in author_info:
            # putting fake ASIN because real one isn't needed -- idk why it's required at all
            data['authorBios']['authors'].append({'class': 'authorBio', 'name': author['name'], 'bio': author['bio'],
                                                  'imageUrl': author['image_url'], 'asin': 'XXXXXXXXXX'})

        if self._author_recommendations is not None:
            data['authorRecs'] = {'class': 'featuredRecommendationList', 'recommendations': self._author_recommendations}
            # since we're using the same recommendations from the end actions,
            # we need to replace the class to match what the kindle expects
            for rec in data['authorRecs']['recommendations']:
                rec['class'] = 'recommendation'

        desc = self._get_book_info_from_tooltips((self._goodreads_book_id, book_image_url))
        if len(desc) > 0:
            data['bookDescription'] = desc[0]
            data['currentBook'] = data['bookDescription']

        data['grokShelfInfo']['asin'] = self._asin

        if reading_info:
            data['readingPages']['pagesInBook'] = reading_info['num_pages']
            for locale, formatted_time in list(data['readingTime']['formattedTime'].items()):
                data['readingTime']['formattedTime'][locale] = formatted_time.format(str(reading_info['hours']),
                                                                                     str(reading_info['minutes']))
        else:
            data['readingPages'] = None
            data['readingTime'] = None

        return start_actions

    def _compile_end_actions(self, end_actions, author_info, cust_recommendations, book_image_url):
        '''Compiles end actions data into dict'''
        timestamp = int((datetime.datetime.now() - datetime.datetime(1970, 1, 1)).total_seconds())

        end_actions['bookInfo']['asin'] = self._asin
        end_actions['bookInfo']['timestamp'] = timestamp
        end_actions['bookInfo']['imageUrl'] = book_image_url

        data = end_actions['data']
        for author in author_info:
            data['authorBios']['authors'].append({'class': 'authorBio', 'name': author['name'],
                                                  'bio': author['bio'], 'imageUrl': author['image_url']})

        if self._author_recommendations is not None:
            data['authorRecs'] = {'class': 'featuredRecommendationList', 'recommendations': self._author_recommendations}
        if cust_recommendations is not None:
            data['customersWhoBoughtRecs'] = {'class': 'featuredRecommendationList', 'recommendations': cust_recommendations}

        return end_actions

    def get_characters(self, entity_id):
        '''Gets book's character data'''
        if self._page_source is None:
            return

        characters = self._page_source.xpath('//div[@class="clearFloats" and contains(., "Characters")]//div[@class="infoBoxRowItem"]//a')
        character_data = {}
        for char in characters:
            if '/characters/' not in char.get('href'):
                continue
            resp = open_url(self._connection, char.get('href'))

            if not resp:
                continue

            char_page = html.fromstring(resp)
            if char_page is None:
                continue

            desc = char_page.xpath('//div[@class="workCharacterAboutClear"]/text()')
            if desc and re.sub(r'\s+', ' ', desc[0]).strip():
                desc = str(re.sub(r'\s+', ' ', desc[0]).strip().encode('latin-1'))
            else:
                desc = u'No description found on Goodreads.'
            alias_list = char_page.xpath('//div[@class="grey500BoxContent" and contains(.,"aliases")]/text()')
            alias_list = [re.sub(r'\s+', ' ', x).strip() for aliases in alias_list for x in aliases.split(',')
                          if re.sub(r'\s+', ' ', x).strip()]
            character_data[entity_id] = {'label': str(char.text),
                                         'description': desc,
                                         'aliases': alias_list}
            entity_id += 1

        if prefs['expand_aliases']:
            characters = {}
            for char, char_data in list(character_data.items()):
                characters[char] = [char_data['label']] + char_data['aliases']

            expanded_aliases = auto_expand_aliases(characters)
            for alias, ent_id in list(expanded_aliases.items()):
                character_data[ent_id]['aliases'].append(alias)

        return character_data

    def get_settings(self, entity_id):
        '''Gets book's setting data'''
        if self._page_source is None:
            return

        settings = self._page_source.xpath('//div[@id="bookDataBox"]/div[@class="infoBoxRowItem"]/a[contains(@href, "/places/")]')
        settings_data = {}
        for setting in settings:
            if '/places/' not in setting.get('href'):
                continue
            label = setting.text
            resp = open_url(self._connection, setting.get('href'))
            if not resp:
                continue
            setting_page = html.fromstring(resp)
            if setting_page is None:
                continue
            desc = setting_page.xpath('//div[@class="mainContentContainer "]/div[@class="mainContent"]/div[@class="mainContentFloat"]/div[@class="leftContainer"]/span/text()')
            if len(desc) > 0 and re.sub(r'\s+', ' ', desc[0]).strip():
                desc = str(re.sub(r'\s+', ' ', desc[0]).strip().encode('latin-1'))
            else:
                desc = u'No description found on Goodreads.'
            settings_data[entity_id] = {'label': str(label),
                                        'description': desc,
                                        'aliases': []}
            entity_id += 1

        return settings_data

    def _get_quotes(self):
        '''Gets book's quote data'''
        if self._page_source is None:
            return

        quotes_page = self._page_source.xpath('//a[@class="actionLink" and contains(., "More quotes")]')
        quotes = []
        if len(quotes_page) > 0:
            resp = open_url(self._connection, quotes_page[0].get('href'))
            if not resp:
                return
            quotes_page = html.fromstring(resp)
            if quotes_page is None:
                return
            for quote in quotes_page.xpath('//div[@class="quoteText"]'):
                quotes.append(re.sub(r'\s+', ' ', quote.text).strip().decode('ascii', 'ignore'))
        else:
            for quote in self._page_source.xpath('//div[@class=" clearFloats bigBox" and contains(., "Quotes from")]//div[@class="bigBoxContent containerWithHeaderContent"]//span[@class="readable"]'):
                quotes.append(re.sub(r'\s+', ' ', quote.text).strip().decode('ascii', 'ignore'))

        return quotes

    def _get_author_info(self):
        '''Gets book's author's data'''
        author_info = []
        if self._page_source is None:
            return

        for author in self._page_source.xpath('//div[@id="bookAuthors"]/span[@itemprop="author"]//a'):
            author_span = author.find('span[@itemprop="name"]')
            if author_span is None:
                continue
            author_name = author_span.text.strip()
            author_page = author.get('href')
            if author_name and author_page:
                author_info.append({'name': author_name, 'url': author_page})
        return author_info

    def _read_primary_author_page(self, author_info):
        '''Rreads primary author's page and gets his/her bio, image url, and image encoded into base64'''
        author = author_info[0]
        author['page'] = html.fromstring(open_url(self._connection, author['url']))
        author['bio'] = self._get_author_bio(author['page'])
        author['image_url'], author['encoded_image'] = self._get_author_image(author['page'], encode_image=True)

    def _read_secondary_author_pages(self, author_info):
        '''Reads secondary authors' page and gets their bios, image urls, and images encoded into base64'''
        if len(author_info) < 2:
            return

        for author in author_info[1:]:
            author['page'] = html.fromstring(open_url(self._connection, author['url']))
            author['bio'] = self._get_author_bio(author['page'])
            author['image_url'] = self._get_author_image(author['page'])

    @staticmethod
    def _get_author_bio(author_page):
        '''Gets author's bio from given page'''
        author_bio = author_page.xpath('//div[@class="aboutAuthorInfo"]/span')
        if not author_bio:
            return None

        author_bio = author_bio[1] if len(author_bio) > 1 else author_bio[0]

        return str(re.sub(r'\s+', ' ', author_bio.text_content()).strip().encode('latin-1'))

    @staticmethod
    def _get_author_image(author_page, encode_image=False):
        '''Gets author's image url and image encoded into base64 from given page'''
        image_url = author_page.xpath('//a[contains(@href, "/photo/author/")]/img')

        if encode_image:
            if not image_url:
                return None, None
            image = urlopen(image_url[0].get('src')).read()
            encoded_image = base64.b64encode(image)
            return image_url[0].get('src'), encoded_image
        else:
            if not image_url:
                return None
            return image_url[0].get('src')

    def _get_author_other_books(self, author_info):
        '''Gets author's other books from given page'''
        if len(author_info) == 0:
            return

        book_info = []

        for book in author_info[0]['page'].xpath('//tr[@itemtype="http://schema.org/Book"]'):
            book_id = book.find('td//div[@class="u-anchorTarget"]').get('id')

            # don't want to add the current book to the other books list
            if book_id == self._goodreads_book_id:
                continue

            image_url = book.find('td//img[@class="bookCover"]').get('src').split('/')
            image_url = '{0}/{1}l/{2}'.format('/'.join(image_url[:-2]), image_url[-2][:-1], image_url[-1])

            book_info.append((book_id, image_url))

        self._author_recommendations = self._get_book_info_from_tooltips(book_info)
        self._author_other_books = [{'e': 1, 't': info['title'], 'a': info['asin']} for info in self._author_recommendations]

    def _get_customer_recommendations(self):
        '''Gets customer recommendations from current book'''
        if self._page_source is None:
            return

        book_info = []
        for book in self._page_source.xpath('//div[@class="bookCarousel"]/div[@class="carouselRow"]/ul/li/a'):
            book_url = book.get('href')
            book_id_search = BOOK_ID_PAT.search(book_url)
            book_id = book_id_search.group(1) if book_id_search else None

            if book_id and book_id != self._goodreads_book_id:
                image_url = book.find('img').get('src')
                book_info.append((book_id, image_url))

        return self._get_book_info_from_tooltips(book_info)

    def _get_book_info_from_tooltips(self, book_info):
        '''Gets books ASIN, title, authors, image url, description, and rating information'''
        if isinstance(book_info, tuple):
            book_info = [book_info]
        books_data = []
        link_pattern = 'resources[Book.{0}][type]=Book&resources[Book.{0}][id]={0}'
        tooltips_page_url = '/tooltips?' + "&".join([link_pattern.format(book_id) for book_id, image_url in book_info])
        tooltips_page_info = json.loads(open_url(self._connection, tooltips_page_url))['tooltips']

        for book_id, image_url in book_info:
            book_data = tooltips_page_info['Book.{0}'.format(book_id)]
            if not book_data:
                continue
            book_data = html.fromstring(book_data)
            parsed_data = self._parse_tooltip_info(book_data, book_id, image_url)
            if not parsed_data:
                continue
            books_data.append(parsed_data)

        return books_data

    def _parse_tooltip_info(self, book_data, book_id, image_url):
        '''Takes information retried from goodreads tooltips link and parses it'''
        title = book_data.xpath('//a[contains(@class, "readable")]')
        title = title[0].text if len(title) > 0 else None
        authors = book_data.xpath('//a[contains(@class, "authorName")]')
        authors = [authors[0].text] if len(authors) > 0 else None
        rating_info = book_data.xpath('//div[@class="bookRatingAndPublishing"]/span[@class="minirating"]')
        if len(rating_info) > 0:
            rating_string = rating_info[0].text_content().strip().replace(',', '').split()
            rating = float(rating_string[rating_string.index('avg')-1])
            num_of_reviews = int(rating_string[-2])
        else:
            rating = None
            num_of_reviews = None

        try:
            asin_elements = book_data.xpath('//a[contains(@class, "kindlePreviewButtonIcon")]/@href')
            book_asin = urllib.parse.parse_qs(urllib.parse.urlsplit(asin_elements[0]).query)["asin"][0]
        except (KeyError, IndexError):
            book_asin = None

        # We should get the ASIN from the tooltips file, but just in case we'll
        # keep this as a fallback (though this only works in some regions - just USA?)
        if not book_asin:
            asin_data_page = open_url(self._connection, '/buttons/glide/' + book_id)
            book_asin = GOODREADS_ASIN_PAT.search(asin_data_page)
            if not book_asin:
                return None
            book_asin = book_asin.group(1)

        desc = book_data.xpath('//div[@class="addBookTipDescription"]//span[not(contains(@id, "freeTextContainer"))]')
        desc_backup = book_data.xpath('//div[@class="addBookTipDescription"]//span[contains(@id, "freeTextContainer")]')
        if len(desc) > 0:
            desc = re.sub(r'\s+', ' ', desc[0].text).strip()
        elif len(desc_backup) > 0:
            desc = re.sub(r'\s+', ' ', desc_backup[0].text).strip()
        else:
            return None

        return {'class': 'featuredRecommendation',
                'asin': book_asin,
                'title': title,
                'authors': authors,
                'imageUrl': image_url,
                'description': desc,
                'hasSample': False,
                'amazonRating': rating,
                'numberOfReviews': num_of_reviews}

    def _get_book_image_url(self):
        '''Gets book's image url'''
        image_url = self._page_source.xpath('//div[contains(concat(" ", @class, " "), " mainContent ")]//div[@id="imagecol"]//img[@id="coverImage"]')
        if len(image_url) > 0:
            return image_url[0].get('src')
        return None

    def _get_num_pages_and_reading_time(self):
        '''Gets book's number of pages and time to read'''
        if self._page_source is None:
            return None

        num_pages = self._page_source.xpath('//span[@itemprop="numberOfPages"]')
        if len(num_pages) > 0:
            num_pages = int(num_pages[0].text.split()[0])
            total_minutes = num_pages * 2
            hours = old_div(total_minutes, 60)
            reading_info = {'num_pages': num_pages, 'hours': hours, 'minutes': total_minutes - (hours * 60)}

            return reading_info
        return None
