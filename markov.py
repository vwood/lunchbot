#!/usr/bin/env python2

"""A really simple IRC bot."""

import re
import sys
import random
import cPickle
from twisted.internet import reactor, protocol
from twisted.words.protocols import irc

class Counter(dict):
    """A stripped down version of collections.Counter for python 2.6 (since cygwin hasn't updated.)."""
    def __init__(self, *args, **kwargs):
        dict.__init__(self)
        self.total_count = 0
        self.update(*args, **kwargs)

    def __missing__(self, key):
        'Items not found have 0 count.'
        return 0

    def __setitem__(self, key, value):
        self.total_count += value - self[key]
        dict.__setitem__(self, key, value)

    def total(self):
        return self.total_count

    def update(self, *args, **kwargs):
        for k,v in dict(*args, **kwargs).iteritems():
            self[k] = v

class Markov(object):
    def __init__(self, filename):
        self.loaded_pickle = False
        try:
            self.model = cPickle.load(open(filename, 'rb'))
            self.loaded_pickle = True
        except:
            self.model = {}

    def save(self, filename):
        cPickle.dump(self.model, open(filename, 'wb'))

    def learn(self, xs):
        for i in xrange(0,len(xs) - 1):
            k = (xs[i],xs[i+1])
            if k not in self.model:
                self.model[k] = Counter()
            if i < len(xs) - 2:
                self.model[k][xs[i+2]] += 1
            else:
                self.model[k][None] += 1

    def seed(self, ys):
        return random.choice([x for x in self.model.keys() if x[0] in ys])

    def random_select(self, key):
        index = random.randint(0, self.model[key].total() - 1)
        for k, v in self.model[key].iteritems():
            if v > index:
                return k
            index -= v
            
    def emit(self, s):
        resp = []
        while s in self.model:
            ts = self.model[s]
            if not len(ts):
                break
            else:
                t = self.random_select(s)
                if not t:
                    break
                resp += [t]
                s = (s[1],t)
        return resp

class BulkLoader(object):
    def __init__(self):
        self.forward = Markov('markov.forward.pickle')
        self.reverse = Markov('markov.reverse.pickle')

    def add(self, filename):
        for line in open(filename, 'r'):
            words = line.split(' ')
            try:
                self.forward.learn(words)
                words.reverse()
                self.reverse.learn(words)
            except:
                print 'Failed to learn %s' % line

    def save(self):
        self.forward.save('markov.forward.pickle')
        self.reverse.save('markov.reverse.pickle')

class Bot(irc.IRCClient):
    """A Markov Chain bot."""
    def _get_nickname(self):
        return self.factory.nickname
    nickname = property(_get_nickname)

    def signedOn(self):
        self.join(self.factory.channel)
        print "Signed on as %s." % self.nickname

    def joined(self, channel):
        print "Joined %s." % channel
        self.msg(channel, 'Hello.')
        
    def __init__(self):
        self.forward = Markov('markov.forward.pickle')
        self.reverse = Markov('markov.reverse.pickle')
        self.load_nouns("nounlist.txt")
        if not self.forward.loaded_pickle and not self.reverse.loaded_pickle:
            self.load_text("seed.txt")
        self.output = False

    def privmsg(self, user, channel, msg):
        if 'bot' in user:
            return
        
        if msg == '!enable':
            self.output = not self.output
            self.msg(channel, 'Output to channel: %s' % self.output)
            return

        if msg == '!save':
            self.forward.save('markov.forward.pickle')
            self.reverse.save('markov.reverse.pickle')
            self.msg(channel, 'I feel pickled.')
            return

        words = self.irc_to_list(msg)
        self.learn(words)

        should_respond = 0.4
        if self.nickname in msg:
            should_respond += 1

        words_pri = self.prioritise_words(words)

        if random.uniform(0,1) < should_respond:
            print "Attempting to respond, using '%s'" % (", ".join(words_pri),)
            resps = [x for x in [self.make_response(words_pri) for x in xrange(1,15)] if x]

            if not len(resps):
                print "No possible responses."
                return

            ideal_score = 0.8
            def score(response,w):
                r = list(set(response) & self.nouns)
                s = abs( len([x for x in r if x in w]) / ( len(r) + 0.1 ) - ideal_score )
                print str(s), ":", " ".join(response)
                return s
            resp = min(resps, key=lambda x: score(x, words_pri))
            self.msg(channel, self.list_to_irc(resp))

    def irc_to_list(self, msg):
        'Convert an irc message to a list of words.'
        words = re.findall('([\/!\w\.]*[!\w\'\.]?[\w]+|,|:|\.|!|\'|\?)', msg)
        if len(words) > 1 and words[1] in ':,':
            words = words[2:]
        return words
                
    def list_to_irc(self, words):
        'Convert a list of words to an irc message.'
        return ' '.join(words).replace(' ,', ',').replace(' ?','?').replace(' !','!').replace(' .', '.').replace(' :', ':')
    
    def learn(self, words):
        'Learns a list of words.'
        self.forward.learn(words)
        self.reverse.learn([x for x in reversed(words)])

    def make_response(self,words):
        try:
            s = self.forward.seed(words)
            rev = self.reverse.emit((s[1],s[0]))
            rev.reverse()
            fwd = self.forward.emit(s)
            return rev + [s[0],s[1]] + fwd
        except Exception as e:
            print "Can't respond: ", e
            return None

    def load_text(self, seedfile):
        try:
            for line in open(seedfile):
                words = self.irc_to_list(line)
                self.learn(words)
            print "Loaded seedfile:", seedfile
        except IOError as e:
            print "Can't load seedfile:", e

    def load_nouns(self, nounfile):
        try:
            self.nouns = set()
            for l in open(nounfile):
                self.nouns.add(l.strip())
        except IOError as e:
            print "Can't load nounfile:", e

    def prioritise_words(self, words):
        words2 = set(words)
        words3 = words2 & self.nouns
        if len(words3) > 0:
            return list(words3)
        return words

class BotFactory(protocol.ClientFactory):
    protocol = Bot
    
    def __init__(self, channel, nickname):
        self.channel = channel
        self.nickname = nickname

    def clientConnectionLost(self, connector, reason):
        print "Connection lost. Reason: %s" % reason
        connector.connect()

    def clientConnectionFailed(self, connector, reason):
        print "Connection failed. Reason: %s" % reason

if __name__ == "__main__":
    def get_arg(argname, default=None):
        try:
            if argname in sys.argv:
                result = sys.argv[sys.argv.index(argname) + 1]
                return result
            return default
        except IndexError:
            print "Argument is missing: %s <option>" % (argname,)
            exit(-1)

    name = get_arg("--name", "mark_v_bot")
    channel = get_arg("--channel", "#dullbots")
    server = get_arg("--server", "irc.freenode.net")
    port = int(get_arg("--port", 6667))
    
    reactor.connectTCP(server, port, BotFactory(channel, name))
    reactor.run()
