#coding=utf-8
import numpy as np
import tensorflow as tf

# Define parameters
FLAGS = tf.app.flags.FLAGS
tf.app.flags.DEFINE_float('learning_rate', 0.00003, 'Initial learning rate.')
tf.app.flags.DEFINE_integer('steps_to_validate', 1000,
                     'Steps to validate and print loss')

# For distributed
tf.app.flags.DEFINE_string("ps_hosts", "",
                           "Comma-separated list of hostname:port pairs")
tf.app.flags.DEFINE_string("worker_hosts", "",
                           "Comma-separated list of hostname:port pairs")
tf.app.flags.DEFINE_string("job_name", "", "One of 'ps', 'worker'")
tf.app.flags.DEFINE_integer("task_index", 0, "Index of task within the job")
tf.app.flags.DEFINE_integer("issync", 0, "是否采用分布式的同步模式，1表示同步模式，0表示异步模式")

# Hyperparameters
learning_rate = FLAGS.learning_rate
steps_to_validate = FLAGS.steps_to_validate

PS_NUM = len(FLAGS.ps_hosts.split(","))
WORKER_NUM = len(FLAGS.worker_hosts.split(","))
SAVE_CHECKPOINT_DIR = "./checkpoint/"

def create_done_queue(i):
  """Queue used to signal death for i'th ps shard. Intended to have 
  all workers enqueue an item onto it to signal doneness."""

  with tf.device("/job:ps/task:%d" % (i)):
    return tf.FIFOQueue(WORKER_NUM, tf.int32, shared_name="done_queue" +
                                                             str(i))


def create_done_queues():
  return [create_done_queue(i) for i in range(PS_NUM)]

def main(_):
  # config = tf.ConfigProto()
  # config.gpu_options.allow_growth = True

  if FLAGS.job_name == 'ps':
    config = tf.ConfigProto(
      device_count={'CPU': 1, 'GPU': 0},
      allow_soft_placement=True,
      log_device_placement=False
    )
    print ('ps using CPU')
  else:
    config = tf.ConfigProto(
      allow_soft_placement=True,
      log_device_placement=False,
    )
    config.gpu_options.allow_growth = True

  if FLAGS.job_name == 'worker' and FLAGS.task_index == 0:
    import shutil
    shutil.rmtree(SAVE_CHECKPOINT_DIR)

  ps_hosts = FLAGS.ps_hosts.split(",")
  worker_hosts = FLAGS.worker_hosts.split(",")
  cluster = tf.train.ClusterSpec({"ps": ps_hosts, "worker": worker_hosts})
  server = tf.train.Server(cluster,job_name=FLAGS.job_name,task_index=FLAGS.task_index
                           ,config=config)

  issync = FLAGS.issync
  if FLAGS.job_name == "ps":

    # with tf.device('/cpu:0'):
    sess = tf.Session(server.target)
    queue = create_done_queue(FLAGS.task_index)

    # wait until all workers are done
    for i in range(WORKER_NUM):
      sess.run(queue.dequeue())
      print("ps %d received done %d" % (FLAGS.task_index, i))

    print("ps %d: quitting" % (FLAGS.task_index))

  elif FLAGS.job_name == "worker":
    with tf.device(tf.train.replica_device_setter(
                    worker_device="/job:worker/task:%d" % FLAGS.task_index,
                    cluster=cluster)):
      global_step = tf.Variable(0, name='global_step', trainable=False)

      input = tf.placeholder("float")
      label = tf.placeholder("float")

      weight = tf.get_variable("weight", [1], tf.float32, initializer=tf.random_normal_initializer())
      biase = tf.get_variable("biase", [1], tf.float32, initializer=tf.random_normal_initializer())
      pred = tf.multiply(input, weight) + biase

      loss_value = loss(label, pred)
      optimizer = tf.train.GradientDescentOptimizer(learning_rate)

      grads_and_vars = optimizer.compute_gradients(loss_value)

      for grad, var in grads_and_vars:
        print ('grad_device:', grad.device, '\tvar_device:',var.device)

      if issync == 1:
        #同步模式计算更新梯度
        rep_op = tf.train.SyncReplicasOptimizer(optimizer,
                                                replicas_to_aggregate=len(
                                                  worker_hosts),
                                                #replica_id=FLAGS.task_index,
                                                total_num_replicas=len(
                                                  worker_hosts),
                                                use_locking=True)
        train_op = rep_op.apply_gradients(grads_and_vars,
                                       global_step=global_step)
        init_token_op = rep_op.get_init_tokens_op()
        chief_queue_runner = rep_op.get_chief_queue_runner()
      else:
        #异步模式计算更新梯度
        train_op = optimizer.apply_gradients(grads_and_vars,
                                       global_step=global_step)

      # if issync:
      #init_op = tf.initialize_all_variables()
      init_op = tf.global_variables_initializer()
      
      saver = tf.train.Saver()
      tf.summary.scalar('cost', loss_value)
      summary_op = tf.summary.merge_all()

      enq_ops = []
      for q in create_done_queues():
        qop = q.enqueue(1)
        enq_ops.append(qop)
 
    sv = tf.train.Supervisor(is_chief=(FLAGS.task_index == 0),
                            logdir= SAVE_CHECKPOINT_DIR,
                            init_op=init_op,
                            summary_op=None,
                            saver=saver,
                            global_step=global_step,
                            save_model_secs=60)

    with sv.prepare_or_wait_for_session(server.target) as sess:
      # 如果是同步模式
      if FLAGS.task_index == 0 and issync == 1:
        sv.start_queue_runners(sess, [chief_queue_runner])
        sess.run(init_token_op)
      step = 0
      while  step < 100000:
        train_x = np.random.randn(1)
        train_y = 2 * train_x + np.random.randn(1) * 0.33  + 10
        _, loss_v, step = sess.run([train_op, loss_value,global_step], feed_dict={input:train_x, label:train_y})
        if step % steps_to_validate == 0:
          w,b = sess.run([weight,biase])
          print("step: %d, weight: %f, biase: %f, loss: %f" %(step, w, b, loss_v))

      for op in enq_ops:
        sess.run(op)

    sv.stop()

def loss(label, pred):
  return tf.square(label - pred)



if __name__ == "__main__":
  try:
    tf.app.run()
  except Exception as e:
    print ('error occured: {}'.format(e))
  finally:
    print ('Finished')

















