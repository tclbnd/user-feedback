"""A simulated REST source with durable tokens, scripted faults, and HTTP-like status codes."""
import json
from pathlib import Path
import os


class Source:
    def __init__(self, spec, state):
        self.spec=spec
        self.state=state
        self.status=json.loads(state.read_text()) if state.exists() else {"current":0,"hits":{}}

    def save(self):
        self.state.parent.mkdir(parents=True,exist_ok=True)
        temp=self.state.with_suffix(".tmp")
        temp.write_text(json.dumps(self.status)+"\n")
        os.replace(temp,self.state)

    def latest(self):
        return self.spec["tokens"][self.status["current"]]

    def fetch(self,token,cursor,if_none_match=None):
        if token not in self.spec["pages"]:
            return 404,{}
        key=cursor if cursor is not None else "initial"
        page=self.spec["pages"][token].get(key)
        if page is None:
            return 404,{}
        identifier=token+":"+key
        n=self.status["hits"].get(identifier,0)
        self.status["hits"][identifier]=n+1
        rule=page.get("faults",[])
        if n < len(rule):
            action=rule[n]
            if action==410:
                self.status["current"]=min(self.status["current"]+1,len(self.spec["tokens"])-1)
            self.save()
            return action,{"retry_after":0}
        self.save()
        etag=page["etag"]
        if etag==if_none_match:
            return 304,{"etag":etag}
        return 200,{"etag":etag,"items":page["items"],"next":page["next"]}
