from datetime import datetime
from django.core.files.temp import NamedTemporaryFile
from whatstrending.models import Article
import os
from django.core.files import File
import urllib
from urllib.parse import urlparse
import urllib.request
import pandas as pd
import numpy as np

from nltk.corpus import stopwords
from sklearn.feature_extraction.text import TfidfVectorizer
from nltk.tokenize import RegexpTokenizer
import re
from bs4 import BeautifulSoup as soup
import requests

from sklearn.metrics.pairwise import cosine_similarity
from gensim.models import Word2Vec
from gensim.models import KeyedVectors
import gensim.downloader as api
import ssl

def update_articles(link, title, descr, image):
    article = Article()
    article.link = link
    article.title = title
    article.descr = descr[:250] + "..."
    
    #image
    name = urlparse(image).path.split('/')[-1]
    ssl._create_default_https_context = ssl._create_unverified_context
    img_temp = NamedTemporaryFile(delete=True)
    img_temp.write(urllib.request.urlopen(image).read())
    img_temp.flush()

    article.image.save(name, File(img_temp), save=True)
    article.save()

def _removeNonAscii(s):
        return "".join(i for i in s if ord(i) < 128)


def make_lower_case(text):
    return text.lower()


def remove_stop_words(text):
    text = text.split()
    stops = set(stopwords.words("english"))
    text = [w for w in text if not w in stops]
    text = " ".join(text)
    return text


def remove_html(text):
    html_pattern = re.compile('<.*?>')
    return html_pattern.sub(r'', text)


def remove_punctuation(text):
    tokenizer = RegexpTokenizer(r'\w+')
    text = tokenizer.tokenize(text)
    text = " ".join(text)
    return text

def update():
    nbc_url="https://www.nbcnews.com/"
    r = requests.get(nbc_url)
    b = soup(r.content, features="html.parser")


    links = []
    for news in b.findAll('h2', {'class': 'tease-card__headline'}):
        links.append(news.a['href'])

    for news in b.findAll('h2', {'class': 'styles_headline__ice3t'}):
        links.append(news.a['href'])

    descr = []
    title = []
    news_articles = {}
    for link in links:
        try:
            page = requests.get(link)
            bsobj = soup(page.content, features="html.parser")

            text = bsobj.find('h1', {'class': 'article-hero-headline__htag'}).text.strip()

            article = ""
            for news in bsobj.findAll('div', {'class': 'article-body__content'}):
                article += news.text.strip()

            image = bsobj.find('picture', {'class': 'article-hero__main-image'}).find('img')['src']
            news_articles[text] = {"link": link, "description": article, "image": image}
            
            title.append(text)
            descr.append(article)
        except:
            continue

    descr = list(set(descr))
    title = list(set(title))

    col = ["Title", "Desc"]
    csv_path = os.path.join(os.path.dirname(__file__), 'news.csv')
    df = pd.read_csv(csv_path, delimiter="_", names=col)

    for i in range(5, len(descr)+5):
        if descr[i-5] and title[i-5]:
            df.loc[i] = [title[i-5], descr[i-5]]

    df['cleaned'] = df['Desc'].apply(_removeNonAscii)

    df['cleaned'] = df.cleaned.apply(func=make_lower_case)
    df['cleaned'] = df.cleaned.apply(func=remove_stop_words)
    df['cleaned'] = df.cleaned.apply(func=remove_punctuation)
    df['cleaned'] = df.cleaned.apply(func=remove_html)

    corpus = []
    user_words = []

    count = 0
    for words in df['cleaned']:
        corpus.append(words.split())
        if(count < 5):
            user_words.append(words.split())
        count += 1

    # Downloading the Google pretrained Word2Vec Model
    path = api.load("word2vec-google-news-300", return_path=True)

    EMBEDDING_FILE = path
    google_word2vec = KeyedVectors.load_word2vec_format(EMBEDDING_FILE, binary=True)

    # Training our corpus with Google Pretrained Model

    google_model = Word2Vec(vector_size=300, window=5, min_count=2, workers=-1)
    google_model.build_vocab(corpus)

    google_model.wv.vectors_lockf = np.ones(len(google_model.wv))

    google_model.wv.intersect_word2vec_format(EMBEDDING_FILE, binary=True)

    google_model.train(corpus, total_examples=google_model.corpus_count, epochs=5)

    # Building TFIDF model and calculate TFIDF score

    tfidf = TfidfVectorizer(analyzer='word', ngram_range=(1, 3), min_df=5, stop_words='english')
    tfidf.fit(df['cleaned'])

    tfidf_list = dict(zip(tfidf.get_feature_names(), list(tfidf.idf_)))
    tfidf_feature = tfidf.get_feature_names()  # tfidf words/col-names

    # Storing the TFIDF Word2Vec embeddings
    tfidf_vectors = [];
    line = 0;
    # for each article
    for desc in corpus:
        sent_vec = np.zeros(300)
        weight_sum = 0;
        # for each word in the article
        for word in desc:
            if word in google_model.wv and word in tfidf_feature:
                vec = google_model.wv[word]
                tf_idf = tfidf_list[word] * (desc.count(word) / len(desc))
                sent_vec += (vec * tf_idf)
                weight_sum += tf_idf
        if weight_sum != 0:
            sent_vec /= weight_sum
        tfidf_vectors.append(sent_vec)
        line += 1

    tfidf_vectors = tfidf_vectors[5:]

    # taking the title and store in new data frame called news
    news = df['Title']
    # remove first 5 articles
    news = news.shift(-5)
    news = news.iloc[:-5]
    cosine_similarities = cosine_similarity(tfidf_vectors, tfidf_vectors)

    recommend = {}

    for idx in range(5):
        sim_scores = list(enumerate(cosine_similarities[idx]))
        sim_scores = sorted(sim_scores, key=lambda x: x[1], reverse=True)
        sim_scores = sim_scores[0:5]
        news_indices = [i[0] for i in sim_scores]
        recommend = news.iloc[news_indices]

    Article.objects.all().delete()
    for i in recommend:
        update_articles(news_articles[i]["link"], i, news_articles[i]["description"], news_articles[i]["image"])
