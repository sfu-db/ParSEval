{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "COUNT",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "VARCHAR"
        }
      ],
      "type": "BIGINT",
      "name": "EXPR$0"
    }
  ],
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 35,
          "name": "$35",
          "type": "VARCHAR"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "AND",
            "operator": "AND",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 33,
                    "name": "$33",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "LITERAL",
                    "value": "Los Angeles",
                    "type": "VARCHAR",
                    "nullable": false,
                    "precision": -1
                  }
                ]
              },
              {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 51,
                    "name": "$51",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "LITERAL",
                    "value": 0,
                    "type": "INTEGER",
                    "nullable": false,
                    "precision": 10
                  }
                ]
              },
              {
                "kind": "LESS_THAN",
                "operator": "<",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "DIVIDE",
                    "operator": "/",
                    "type": "FLOAT",
                    "operands": [
                      {
                        "kind": "TIMES",
                        "operator": "*",
                        "type": "REAL",
                        "operands": [
                          {
                            "kind": "CAST",
                            "operator": "CAST",
                            "type": "REAL",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 19,
                                "name": "$19",
                                "type": "FLOAT"
                              }
                            ]
                          },
                          {
                            "kind": "LITERAL",
                            "value": 100,
                            "type": "INTEGER",
                            "nullable": false,
                            "precision": 10
                          }
                        ]
                      },
                      {
                        "kind": "CASE",
                        "operator": "CASE",
                        "type": "FLOAT",
                        "operands": [
                          {
                            "kind": "EQUALS",
                            "operator": "=",
                            "type": "BOOLEAN",
                            "operands": [
                              {
                                "kind": "INPUT_REF",
                                "index": 18,
                                "name": "$18",
                                "type": "FLOAT"
                              },
                              {
                                "kind": "LITERAL",
                                "value": 0.0,
                                "type": "FLOAT",
                                "nullable": false,
                                "precision": 15
                              }
                            ]
                          },
                          {
                            "kind": "LITERAL",
                            "value": "NULL",
                            "type": "FLOAT",
                            "nullable": true,
                            "precision": 15
                          },
                          {
                            "kind": "INPUT_REF",
                            "index": 18,
                            "name": "$18",
                            "type": "FLOAT"
                          }
                        ]
                      }
                    ]
                  },
                  {
                    "kind": "LITERAL",
                    "value": 0.18,
                    "type": "DECIMAL",
                    "nullable": false,
                    "precision": 3
                  }
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 29,
                    "name": "$29",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "frpm",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}