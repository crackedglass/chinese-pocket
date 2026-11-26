import os
import sys
from pathlib import Path
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from chinese_bot.store import Store

OWNER_ID = 842863036
VOCAB = [
 ["我","wǒ","я; меня"],["你","nǐ","ты; тебя"],["他","tā","он; его"],["她","tā","она; её"],["我们","wǒmen","мы; нас"],
 ["这","zhè","этот; это"],["那","nà","тот; то"],["谁","shéi","кто"],["什么","shénme","что; какой"],["哪儿","nǎr","где; куда"],
 ["怎么","zěnme","как"],["是","shì","быть; являться"],["有","yǒu","иметь; есть; находиться"],["不","bù","не; нет"],["没","méi","не иметь; не было"],
 ["好","hǎo","хороший; хорошо"],["大","dà","большой"],["小","xiǎo","маленький"],["多","duō","много; многочисленный"],["少","shǎo","мало; немногочисленный"],
 ["人","rén","человек; люди"],["家","jiā","дом; семья"],["学生","xuésheng","ученик; студент"],["老师","lǎoshī","учитель"],["学校","xuéxiào","школа"],
 ["中国","Zhōngguó","Китай"],["汉语","Hànyǔ","китайский язык"],["朋友","péngyou","друг"],["爸爸","bàba","папа; отец"],["妈妈","māma","мама; мать"],
 ["儿子","érzi","сын"],["女儿","nǚér","дочь"],["名字","míngzi","имя"],["书","shū","книга"],["水","shuǐ","вода"],
 ["茶","chá","чай"],["米饭","mǐfàn","варёный рис"],["苹果","píngguǒ","яблоко"],["狗","gǒu","собака"],["猫","māo","кошка"],
 ["吃","chī","есть; кушать"],["喝","hē","пить"],["看","kàn","смотреть; читать"],["听","tīng","слушать; слышать"],["说","shuō","говорить; сказать"],
 ["读","dú","читать"],["写","xiě","писать"],["去","qù","идти; ехать"],["来","lái","приходить; приезжать"],["做","zuò","делать; изготавливать"]
]

def main():
    db_path = os.environ.get('BOT_DB_PATH','/data/vocabulary.sqlite3')
    store = Store(db_path)
    personal = store.personal_dictionary(OWNER_ID)
    with store.connect() as conn:
        before = conn.execute('SELECT COUNT(*) FROM dictionary_words WHERE dictionary_id=?',(personal,)).fetchone()[0]
    inserted=store.add_dictionary_words(personal,VOCAB)
    with store.connect() as conn:
        after = conn.execute('SELECT COUNT(*) FROM dictionary_words WHERE dictionary_id=?',(personal,)).fetchone()[0]
    print(f'personal_dictionary_id={personal}; inserted={inserted}; total={after}; previous_total={before}')

if __name__=='__main__': main()
