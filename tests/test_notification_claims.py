"""Output claim revocation with an in-memory engine; no OS/media/network I/O."""
import datetime as dt
import unittest
from aiflame.core.engine import Engine
from aiflame.core.events import SourcePolicy, make_event


class ClaimRevocationTests(unittest.TestCase):
    def setUp(self):
        self.now, self.wall = 1000, 1700000000
        self.engine = Engine(':memory:', clock=lambda:self.now, wall=lambda:self.wall,
            sources=[SourcePolicy(source,source,source) for source in ('test','other')])
        self.addCleanup(self.engine.db.close)
        self.engine.update_settings({'desktop_notifications':True,'sound_notifications':True})

    def send(self, state='needs_input', seq=1, task='task'):
        event=make_event('test',task,state,seq)
        event['occurred_at']=dt.datetime.fromtimestamp(self.wall,dt.timezone.utc).isoformat()
        self.engine.accept(event,'test')

    def claim(self, output):
        message,=output.claim()
        self.assertEqual(message['source_id'],'test')
        self.assertEqual(message['task_id'],'task')
        self.assertGreater(message['expires'],self.wall)
        proof={key:message[key] for key in ('event_id','claim','generation')}
        self.assertTrue(output.validate_claim(proof)['valid'])
        return proof

    def test_task_transition_revokes_already_claimed_notification_and_sound(self):
        self.send()
        proofs=[(output,self.claim(output)) for output in (self.engine.desktop,self.engine.sound)]
        self.send('working',2)
        for output,proof in proofs:
            self.assertFalse(output.validate_claim(proof)['valid'])
            status=self.engine.db.execute('SELECT status FROM deliveries WHERE event=? AND output=?',
                (proof['event_id'],output.name)).fetchone()[0]
            self.assertEqual(status,'superseded')

    def test_disable_reenable_and_expiry_invalidate_claims(self):
        for operation in ('channel','source','expiry'):
            for name in ('desktop','sound'):
                with self.subTest(operation=operation,output=name):
                    output=getattr(self.engine,name)
                    self.send(task=operation+name)
                    message,=output.claim()
                    proof={key:message[key] for key in ('event_id','claim','generation')}
                    if operation=='channel':
                        setting='desktop_notifications' if name=='desktop' else 'sound_notifications'
                        self.engine.update_settings({setting:False})
                        self.engine.update_settings({setting:True})
                    elif operation=='source':
                        self.engine.update_settings({'source_enabled':{'test':False,'other':True}})
                        self.engine.update_settings({'source_enabled':{'test':True,'other':True}})
                    else:
                        self.wall+=61
                    self.assertFalse(output.validate_claim(proof)['valid'])
                    # Other outputs' unclaimed messages are irrelevant to the next case.
                    for other in (self.engine.desktop,self.engine.sound):
                        for pending in other.claim():
                            other.acknowledge({key:pending[key] for key in ('event_id','claim')}|{'status':'unavailable'})

    def test_unrelated_settings_and_other_source_do_not_revoke_legitimate_claim(self):
        self.send()
        proofs=[(output,self.claim(output)) for output in (self.engine.desktop,self.engine.sound)]
        self.engine.update_settings({'brightness':5,'source_enabled':{'test':True,'other':False}})
        for output,proof in proofs:
            self.assertTrue(output.validate_claim(proof)['valid'])

    def test_recreated_output_generation_and_expired_proof_cannot_validate(self):
        self.send()
        for name in ('desktop','sound'):
            output=getattr(self.engine,name)
            proof=self.claim(output)
            replacement=type(output)(self.engine)
            self.assertNotEqual(output.generation,replacement.generation)
            self.assertFalse(replacement.validate_claim(proof)['valid'])
