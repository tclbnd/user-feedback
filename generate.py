import json
from pathlib import Path


def make(root):
    root.mkdir(parents=True,exist_ok=True)
    jobs=[]
    truth=[]
    errors=[]
    for i in range(24):
        id=f"task-{i:02}"
        template=f"v{i%3+1}"
        route=i%2==0
        reason={2:"UNKNOWN_TEMPLATE",9:"AMBIGUOUS_FIELD",14:"TIMEOUT",17:"MISSING_FIELD"}.get(i)
        if reason=="UNKNOWN_TEMPLATE":
            template="v9"
        fields=[("Buyer","Name" if i%2 else "Full name",f"{id}-buyer",f"Buyer {i}"),
                ("Contact","Full name",f"{id}-contact",f"Contact {i}")]
        if route:
            fields.append(("Routing","Route code",f"{id}-route",f"R{i}"))
        if reason=="AMBIGUOUS_FIELD":
            fields.append(("Buyer","Name",f"{id}-conflict",f"Conflict {i}"))
        if reason=="MISSING_FIELD":
            fields=fields[1:]
        if i%2:
            fields.reverse()
        html=[]
        for group,label,key,value in fields:
            if i%3==1:
                entry=f'<input value="{value}" id="{key}" aria-label="{label}">'
            else:
                entry=f'<input value="{value}" id="{key}"><label for="{key}">{label}</label>'
            html.append(f'<fieldset><legend>{group}</legend>{entry}</fieldset>')
        html.append('<fieldset hidden><legend>Buyer</legend><input id="ghost" aria-label="Name" value="Ghost"></fieldset>')
        options=[False,False if reason=="TIMEOUT" else True]
        files=[]
        for j,ready in enumerate(options):
            name=f"{id}-{j}.html"
            files.append(name)
            (root/name).write_text(f'<form data-template="{template}" data-ready="{str(ready).lower()}" data-routing="{"on" if route else "off"}">'
                                   +"".join(html)+"</form>")
        jobs.append({"id":id,"snapshots":files})
        if reason:
            errors.append({"id":id,"reason":reason,"file":files[-1]})
        else:
            truth.append({"id":id,"template":template,
                          "fields":{"buyer":f"Buyer {i}","contact":f"Contact {i}","route":f"R{i}" if route else None},
                          "sources":{"buyer":f"{id}-buyer","contact":f"{id}-contact","route":f"{id}-route" if route else None}})
    (root/"jobs.json").write_text(json.dumps({"jobs":jobs})+"\n")
    return {"rows":truth,"review":errors}


if __name__=="__main__":
    make(Path.cwd())
