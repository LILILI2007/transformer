"""Independent, reversible batch sizing and fair queues."""
from collections import deque
from dataclasses import dataclass
from .batching import output_cost,wire_batches

def size_class(source):
    cost=output_cost(source)
    return 'short' if cost<=256 else 'medium' if cost<=1024 else 'long'

@dataclass
class Profile:
    limit:int=20
    successes:int=0
    generation:int=0

class Adaptive:
    def __init__(self,trace):
        self.profiles={};self.trace=trace
    def get(self,scope):return self.profiles.setdefault(scope,Profile())
    def shrink(self,scope,count,reason,batch=''):
        state=self.get(scope);state.limit=max(1,min(state.limit,max(1,count//2)))
        state.successes=0;state.generation+=1
        self.trace({'event':'batch_shrink','scope':list(scope),'mod_id':scope[0],'batch':batch,'max_items':state.limit,'reason':reason})
        return state.limit
    def success(self,scope,count,generation,batch=''):
        state=self.get(scope)
        if state.generation!=generation or count<state.limit:return state.limit
        state.successes+=1
        if state.successes>=3 and state.limit<20:
            state.limit=min(20,max(state.limit+1,(state.limit*3+1)//2));state.successes=0
            state.generation+=1
            self.trace({'event':'batch_grow','scope':list(scope),'mod_id':scope[0],'batch':batch,'max_items':state.limit})
        return state.limit

class FairQueue:
    def __init__(self,by_mod,scopes=None):
        self.queues={};self.order=deque()
        for index,items in enumerate(by_mod):
            for item in items:self.queues.setdefault((index,(scopes or {}).get(item[0]),size_class(item[1])),deque()).append(item)
        self.order.extend(self.queues)
    def __bool__(self):return bool(self.order)
    def estimated_requests(self,max_chars,max_tokens,marker_cost=22):
        return sum(sum(1 for _ in wire_batches(items,20,max_chars,max_tokens,marker_cost)) for items in self.queues.values())
    def pop(self,session):
        identity=self.order.popleft();index=identity[0];queue=self.queues[identity]
        limit=session.limit_for([queue[0]])
        batch=[]
        while queue and len(batch)<limit:
            proposed=batch+[queue[0]]
            if batch and sum(1 for _ in wire_batches(proposed,limit,session.batch_chars,session.config.max_output_tokens,getattr(session,'marker_cost',22)))>1:break
            batch.append(queue.popleft())
        if queue:self.order.append(identity)
        if any(scope[0]!=index for scope in self.order):
            while self.order[0][0]==index:self.order.rotate(-1)
        return index,batch
