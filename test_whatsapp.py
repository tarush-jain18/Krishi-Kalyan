from app.services.telegram_service import whatsapp_service

response = whatsapp_service.send_text(

    "917683066375",
    #"917011200895",

    "dhriti gendamal h"

)

print(response)