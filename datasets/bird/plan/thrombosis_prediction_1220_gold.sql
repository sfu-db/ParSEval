{
  "relOp": "LogicalAggregate",
  "keys": [],
  "aggs": [
    {
      "operator": "AVG",
      "distinct": false,
      "ignoreNulls": false,
      "operands": [
        {
          "column": 0,
          "type": "FLOAT"
        }
      ],
      "type": "FLOAT",
      "name": "EXPR$0"
    }
  ],
  "id": "8",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 15,
          "name": "$15",
          "type": "FLOAT"
        }
      ],
      "id": "7",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "OR",
            "operator": "OR",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN",
                    "operator": ">",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 15,
                        "name": "$15",
                        "type": "FLOAT"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 6.5,
                        "type": "DECIMAL",
                        "nullable": false,
                        "precision": 2
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
                        "index": 1,
                        "name": "$1",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "F",
                        "type": "VARCHAR",
                        "nullable": false,
                        "precision": -1
                      }
                    ]
                  }
                ]
              },
              {
                "kind": "AND",
                "operator": "AND",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "GREATER_THAN",
                    "operator": ">",
                    "type": "BOOLEAN",
                    "operands": [
                      {
                        "kind": "INPUT_REF",
                        "index": 15,
                        "name": "$15",
                        "type": "FLOAT"
                      },
                      {
                        "kind": "LITERAL",
                        "value": 8.0,
                        "type": "DECIMAL",
                        "nullable": false,
                        "precision": 2
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
                        "index": 1,
                        "name": "$1",
                        "type": "VARCHAR"
                      },
                      {
                        "kind": "LITERAL",
                        "value": "M",
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
                        "index": 8,
                        "name": "$8",
                        "type": "DATE"
                      },
                      {
                        "kind": "SCALAR_QUERY",
                        "operator": "$SCALAR_QUERY",
                        "operands": [],
                        "query": [
                          {
                            "relOp": "LogicalAggregate",
                            "keys": [],
                            "aggs": [
                              {
                                "operator": "MAX",
                                "distinct": false,
                                "ignoreNulls": false,
                                "operands": [
                                  {
                                    "column": 0,
                                    "type": "DATE"
                                  }
                                ],
                                "type": "DATE",
                                "name": "EXPR$0"
                              }
                            ],
                            "id": "2",
                            "inputs": [
                              {
                                "relOp": "LogicalProject",
                                "project": [
                                  {
                                    "kind": "INPUT_REF",
                                    "index": 1,
                                    "name": "$1",
                                    "type": "DATE"
                                  }
                                ],
                                "id": "1",
                                "inputs": [
                                  {
                                    "relOp": "LogicalTableScan",
                                    "table": "Laboratory",
                                    "id": "0",
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
                ]
              }
            ]
          },
          "variableset": "[]",
          "id": "6",
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
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 7,
                    "name": "$7",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "5",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Patient",
                  "id": "3",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
                  "id": "4",
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